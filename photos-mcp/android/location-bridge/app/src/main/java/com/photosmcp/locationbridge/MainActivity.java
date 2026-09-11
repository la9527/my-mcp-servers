package com.photosmcp.locationbridge;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DatePickerDialog;
import android.app.Dialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.res.ColorStateList;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.TextUtils;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.Window;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.GridLayout;
import android.widget.ImageButton;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.DateFormat;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;

public final class MainActivity extends Activity {
    private static final int PERMISSION_REQUEST = 42;
    private static final ZoneId SEOUL_ZONE = ZoneId.of("Asia/Seoul");
    private static final DateTimeFormatter DISPLAY_TIMESTAMP =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm").withZone(SEOUL_ZONE);
    private static final String SELECTION_MODE_BALANCED = "balanced";
    private static final String SELECTION_MODE_PEOPLE_PRESENT = "people_present";
    private static final String SELECTION_MODE_LANDSCAPE = "landscape";

    private int paper;
    private int surface;
    private int card;
    private int ink;
    private int muted;
    private int accent;
    private int onAccent;
    private int softAccent;
    private int outline;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final ExecutorService imageExecutor = Executors.newFixedThreadPool(4);
    private final Handler manualPollingHandler = new Handler(Looper.getMainLooper());
    private FrameLayout contentHost;
    private EditText enrollmentInput;
    private LinearLayout enrollmentSection;
    private Button syncButton;
    private TextView settingsStatus;
    private ProgressBar progress;
    private String currentSection = "home";
    private ImageButton inboxButton;
    private ImageButton settingsButton;
    private WebView storyWebView;
    private PrivateImageCache imageCache;
    private Dialog recommendationViewer;
    private int resultsGeneration = 0;
    private int viewerLoadGeneration = 0;
    private int manualPollingGeneration = 0;
    private Runnable scheduledManualPoll;
    private String resultsStoryId = "";
    private LocalDate manualDateFrom = LocalDate.now(java.time.ZoneId.of("Asia/Seoul"));
    private LocalDate manualDateTo = manualDateFrom;
    private String manualSelectionMode = SELECTION_MODE_BALANCED;
    private boolean firstResume = true;
    private final Map<String, NavigationItem> navigationItems = new LinkedHashMap<>();

    private static final class NavigationItem {
        final LinearLayout root;
        final FrameLayout indicator;
        final ImageView icon;
        final TextView label;
        final String title;
        final int position;

        NavigationItem(LinearLayout root, FrameLayout indicator, ImageView icon,
                       TextView label, String title, int position) {
            this.root = root;
            this.indicator = indicator;
            this.icon = icon;
            this.label = label;
            this.title = title;
            this.position = position;
        }
    }

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        if ((getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
            WebView.setWebContentsDebuggingEnabled(true);
        }
        loadDesignTokens();
        imageCache = new PrivateImageCache(this);
        if (state != null) {
            try {
                manualDateFrom = LocalDate.parse(state.getString("manual_date_from", manualDateFrom.toString()));
                manualDateTo = LocalDate.parse(state.getString("manual_date_to", manualDateTo.toString()));
                manualSelectionMode = normalizedSelectionMode(
                        state.getString("manual_selection_mode", SELECTION_MODE_BALANCED));
            } catch (RuntimeException ignored) {
                manualDateFrom = LocalDate.now(java.time.ZoneId.of("Asia/Seoul"));
                manualDateTo = manualDateFrom;
                manualSelectionMode = SELECTION_MODE_BALANCED;
            }
        }
        setContentView(buildShell());
        showHome();
        handleDebugEnrollment();
        if (new ApiClient(this).isEnrolled()) SyncJobService.schedule(this);
        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,
                    this::handleBack);
        }
    }

    @Override protected void onResume() {
        super.onResume();
        if (firstResume) {
            firstResume = false;
            return;
        }
        // Read-only screens are server projections, not device-owned history.
        // Refresh them whenever the app returns to the foreground so a desktop
        // deletion cannot leave an old Android view visible indefinitely.
        if ("home".equals(currentSection)) {
            showHome();
        } else if ("runs".equals(currentSection)) {
            showRuns();
        } else if ("results".equals(currentSection)) {
            showResults(resultsStoryId);
        } else if ("story".equals(currentSection) && storyWebView == null) {
            showStories();
        }
    }

    private View buildShell() {
        LinearLayout shell = new LinearLayout(this);
        shell.setOrientation(LinearLayout.VERTICAL);
        shell.setBackgroundColor(paper);

        LinearLayout header = new LinearLayout(this);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(16), dp(8), dp(8), dp(8));
        header.setBackgroundColor(surface);
        header.setElevation(dp(1));
        header.setMinimumHeight(dp(64));

        ImageView mark = new ImageView(this);
        mark.setImageResource(R.drawable.ic_photosmcp_mark);
        mark.setColorFilter(accent);
        mark.setContentDescription(null);
        header.addView(mark, new LinearLayout.LayoutParams(dp(32), dp(32)));

        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        titles.setPadding(dp(10), 0, 0, 0);
        TextView title = text(getString(R.string.brand_name), 20, ink);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        TextView subtitle = text(getString(R.string.brand_subtitle), 12, muted);
        titles.addView(title);
        titles.addView(subtitle);
        header.addView(titles, new LinearLayout.LayoutParams(0, -2, 1));

        inboxButton = topAction(R.drawable.ic_notifications, "알림");
        inboxButton.setOnClickListener(v -> showInbox());
        header.addView(inboxButton, new LinearLayout.LayoutParams(dp(48), dp(48)));
        settingsButton = topAction(R.drawable.ic_settings, "설정");
        settingsButton.setOnClickListener(v -> showSettings());
        header.addView(settingsButton, new LinearLayout.LayoutParams(dp(48), dp(48)));

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setIndeterminate(true);
        progress.setIndeterminateTintList(ColorStateList.valueOf(accent));
        progress.setContentDescription("콘텐츠를 불러오는 중");
        progress.setVisibility(View.GONE);

        contentHost = new FrameLayout(this);
        shell.addView(header, new LinearLayout.LayoutParams(-1, -2));
        shell.addView(progress, new LinearLayout.LayoutParams(-1, dp(3)));
        shell.addView(contentHost, new LinearLayout.LayoutParams(-1, 0, 1));
        shell.addView(buildNavigation(), new LinearLayout.LayoutParams(-1, dp(80)));
        shell.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(
                        WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else {
                view.setPadding(
                        insets.getSystemWindowInsetLeft(),
                        insets.getSystemWindowInsetTop(),
                        insets.getSystemWindowInsetRight(),
                        insets.getSystemWindowInsetBottom());
            }
            return insets;
        });
        return shell;
    }

    private View buildNavigation() {
        LinearLayout nav = new LinearLayout(this);
        nav.setPadding(dp(8), dp(4), dp(8), dp(4));
        nav.setBackgroundColor(surface);
        nav.setElevation(dp(8));
        addNavigationItem(nav, "home", "홈", R.drawable.ic_nav_home, 1);
        addNavigationItem(nav, "runs", "작업", R.drawable.ic_nav_work, 2);
        addNavigationItem(nav, "results", "추천", R.drawable.ic_nav_recommend, 3);
        addNavigationItem(nav, "story", "이야기", R.drawable.ic_nav_story, 4);
        updateNavigationSelection();
        return nav;
    }

    private void addNavigationItem(LinearLayout nav, String section, String title,
                                   int iconResource, int position) {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setPadding(dp(4), dp(4), dp(4), dp(3));
        root.setClickable(true);
        root.setFocusable(true);
        root.setBackground(rippleBackground(Color.TRANSPARENT, dp(18)));

        FrameLayout indicator = new FrameLayout(this);
        LinearLayout.LayoutParams indicatorParams = new LinearLayout.LayoutParams(dp(64), dp(32));
        indicatorParams.gravity = Gravity.CENTER_HORIZONTAL;
        indicator.setLayoutParams(indicatorParams);

        ImageView icon = new ImageView(this);
        icon.setImageResource(iconResource);
        icon.setColorFilter(muted);
        icon.setContentDescription(null);
        FrameLayout.LayoutParams iconParams = new FrameLayout.LayoutParams(dp(24), dp(24), Gravity.CENTER);
        indicator.addView(icon, iconParams);

        TextView label = text(title, 12, muted);
        label.setGravity(Gravity.CENTER);
        label.setPadding(0, dp(2), 0, 0);
        root.addView(indicator);
        root.addView(label, new LinearLayout.LayoutParams(-1, -2));

        root.setContentDescription(title + ", 탭 " + position + "/4");
        root.setOnClickListener(v -> {
            switch (section) {
                case "runs": showRuns(); break;
                case "results": showResults(); break;
                case "story": showStories(); break;
                default: showHome();
            }
        });
        navigationItems.put(section, new NavigationItem(
                root, indicator, icon, label, title, position));
        nav.addView(root, new LinearLayout.LayoutParams(0, -1, 1));
    }

    private ImageButton topAction(int iconResource, String label) {
        ImageButton button = new ImageButton(this);
        button.setImageResource(iconResource);
        button.setColorFilter(muted);
        button.setScaleType(ImageView.ScaleType.CENTER);
        button.setPadding(dp(12), dp(12), dp(12), dp(12));
        button.setBackground(rippleBackground(Color.TRANSPARENT, dp(24)));
        button.setContentDescription(label + " 열기");
        button.setTooltipText(label);
        button.setFocusable(true);
        return button;
    }

    private void selectSection(String section) {
        // Each navigation render replaces the content tree, even when the
        // logical section name is unchanged. Never leave a poll bound to a
        // detached status view.
        cancelManualPolling();
        currentSection = section;
        updateNavigationSelection();
    }

    private void cancelManualPolling() {
        manualPollingGeneration++;
        if (scheduledManualPoll != null) {
            manualPollingHandler.removeCallbacks(scheduledManualPoll);
            scheduledManualPoll = null;
        }
    }

    private boolean canPollManualOperation(int generation) {
        return generation == manualPollingGeneration
                && "runs".equals(currentSection)
                && !isFinishing()
                && (Build.VERSION.SDK_INT < 17 || !isDestroyed())
                && !executor.isShutdown();
    }

    private void updateNavigationSelection() {
        for (Map.Entry<String, NavigationItem> entry : navigationItems.entrySet()) {
            boolean selected = entry.getKey().equals(currentSection);
            NavigationItem item = entry.getValue();
            item.root.setSelected(selected);
            item.indicator.setBackground(selected
                    ? roundedBackground(softAccent, dp(18), 0, Color.TRANSPARENT)
                    : null);
            item.icon.setColorFilter(selected ? accent : muted);
            item.label.setTextColor(selected ? accent : muted);
            item.label.setTypeface(Typeface.DEFAULT, selected ? Typeface.BOLD : Typeface.NORMAL);
            item.root.setContentDescription(item.title
                    + (selected ? ", 선택됨" : "") + ", 탭 " + item.position + "/4");
            if (Build.VERSION.SDK_INT >= 30) {
                item.root.setStateDescription(selected ? "선택됨" : "선택되지 않음");
            }
        }
        updateTopAction(inboxButton, "inbox".equals(currentSection));
        updateTopAction(settingsButton,
                "settings".equals(currentSection) || "people".equals(currentSection));
    }

    private void updateTopAction(ImageButton button, boolean selected) {
        if (button == null) return;
        button.setSelected(selected);
        button.setColorFilter(selected ? accent : muted);
        button.setBackground(rippleBackground(
                selected ? softAccent : Color.TRANSPARENT, dp(24)));
        if (Build.VERSION.SDK_INT >= 30) {
            button.setStateDescription(selected ? "선택됨" : "선택되지 않음");
        }
    }

    private void showHome() {
        selectSection("home");
        LinearLayout page = page("오늘의 사진", "사진 정리 상태와 최근 이야기를 한눈에 확인합니다.");
        TextView connection = bodyText("Mac 연결 상태를 확인하고 있어요…");
        View connectionCard = card(connection);
        TextView run = bodyText("");
        View runCard = card(run);
        runCard.setVisibility(View.GONE);
        TextView storyText = bodyText("");
        View storyCard = interactiveCard(storyText, this::showStory);
        storyCard.setVisibility(View.GONE);
        TextView actionText = bodyText("");
        View actionCard = interactiveCard(actionText, this::showInbox);
        actionCard.setVisibility(View.GONE);
        page.addView(connectionCard);
        Button manualStory = primaryButton("날짜로 Story 만들기");
        manualStory.setContentDescription("촬영 날짜를 선택해 새 Story 만들기");
        manualStory.setOnClickListener(v -> showManualCuration());
        page.addView(manualStory, matchWrap());
        page.addView(space(dp(12)));
        page.addView(runCard);
        page.addView(storyCard);
        page.addView(actionCard);
        setScrollable(page);
        if (!new OwnerApiClient(this).canConnect()) {
            connection.setText("휴대폰 연결이 필요해요\n설정에서 GPS Bridge를 등록해 주세요.");
            return;
        }
        setLoading(true);
        executor.execute(() -> {
            try {
                JSONObject data = new OwnerApiClient(this).getDashboard().getJSONObject("data");
                JSONObject latest = data.optJSONObject("latest_run");
                JSONObject story = data.optJSONObject("latest_story");
                String daemon = data.optString("daemon_status", "unknown");
                String connectionSummary = "ready".equals(daemon)
                        ? "Mac과 연결됨\n사진 정리 서비스를 사용할 수 있어요."
                        : "Mac 상태를 확인해 주세요\n현재 상태 · " + daemon;
                String runSummary = "";
                if (latest != null) {
                    runSummary = "최근 사진 정리  ·  " + statusLabel(latest.optString("status"))
                            + "\n분석 " + latest.optInt("processed_count") + "장"
                            + "  ·  추천 " + latest.optInt("recommended_count") + "장"
                            + "\nApple·Google 사진을 하나로 정리합니다.";
                } else {
                    runSummary = "아직 사진 정리 기록이 없어요\n첫 작업이 완료되면 여기에 표시됩니다.";
                }
                String storySummary = "";
                if (story != null) {
                    String peopleCaption = storyPeopleCaption(story);
                    storySummary = "최신 이야기\n" + story.optString("title", "사진 이야기")
                            + "  ·  " + story.optInt("photo_count") + "장"
                            + (peopleCaption.isEmpty() ? "" : "\n" + peopleCaption)
                            + "\n이야기 보기  →";
                }
                int actions = data.optInt("action_required_count");
                String actionSummary = actions > 0
                        ? "확인이 필요한 작업  " + actions + "건\n알림에서 확인하기  →" : "";
                String finalRunSummary = runSummary;
                String finalStorySummary = storySummary;
                runOnUiThread(() -> {
                    connection.setText(connectionSummary);
                    run.setText(finalRunSummary);
                    runCard.setVisibility(View.VISIBLE);
                    if (!finalStorySummary.isEmpty()) {
                        storyText.setText(finalStorySummary);
                        storyCard.setVisibility(View.VISIBLE);
                    }
                    if (!actionSummary.isEmpty()) {
                        actionText.setText(actionSummary);
                        actionCard.setVisibility(View.VISIBLE);
                    }
                    setLoading(false);
                });
            } catch (Exception error) {
                runOnUiThread(() -> { connection.setText(connectionHelp(error)); setLoading(false); });
            }
        });
    }

    private void showRuns() {
        selectSection("runs");
        LinearLayout page = page("사진 정리 작업", "진행 상태와 완료된 작업, 다음 실행으로 넘긴 사진을 확인합니다.");
        Button create = quietButton("새 작업");
        create.setContentDescription("날짜를 선택해 새 사진 분석 작업 만들기");
        create.setOnClickListener(v -> showManualCuration());
        page.addView(create, matchWrap());
        page.addView(space(dp(12)));
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        page.addView(list);
        setScrollable(page);
        loadJsonList("runs", list);
    }

    private void showManualCuration() {
        selectSection("runs");
        LinearLayout page = page(
                "날짜로 Story 만들기",
                "선택한 촬영일의 휴대폰 원본 GPS를 먼저 동기화한 뒤, "
                        + "Apple Photos와 Google Photos를 하나의 이야기로 만듭니다.");

        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        TextView dateLabel = bodyText("촬영 날짜 · 한국시간 · 시작일과 종료일 포함");
        dateLabel.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        form.addView(dateLabel);
        form.addView(space(dp(8)));

        LinearLayout quickDates = new LinearLayout(this);
        quickDates.setOrientation(LinearLayout.HORIZONTAL);
        Button today = quietButton("오늘");
        Button yesterday = quietButton("어제");
        Button sevenDays = quietButton("최근 7일");
        quickDates.addView(today, new LinearLayout.LayoutParams(0, -2, 1));
        quickDates.addView(yesterday, new LinearLayout.LayoutParams(0, -2, 1));
        quickDates.addView(sevenDays, new LinearLayout.LayoutParams(0, -2, 1));
        form.addView(quickDates, matchWrap());
        form.addView(space(dp(8)));

        LinearLayout dates = new LinearLayout(this);
        dates.setOrientation(LinearLayout.HORIZONTAL);
        Button fromButton = quietButton("");
        Button toButton = quietButton("");
        dates.addView(fromButton, new LinearLayout.LayoutParams(0, -2, 1));
        dates.addView(toButton, new LinearLayout.LayoutParams(0, -2, 1));
        form.addView(dates, matchWrap());
        Runnable updateDates = () -> {
            fromButton.setText("시작\n" + manualDateFrom);
            toButton.setText("종료\n" + manualDateTo);
            fromButton.setContentDescription(manualDateFrom + ", 시작일");
            toButton.setContentDescription(manualDateTo + ", 종료일");
        };
        updateDates.run();
        LocalDate now = LocalDate.now(java.time.ZoneId.of("Asia/Seoul"));
        today.setOnClickListener(v -> {
            manualDateFrom = now;
            manualDateTo = now;
            updateDates.run();
        });
        yesterday.setOnClickListener(v -> {
            manualDateFrom = now.minusDays(1);
            manualDateTo = manualDateFrom;
            updateDates.run();
        });
        sevenDays.setOnClickListener(v -> {
            manualDateFrom = now.minusDays(6);
            manualDateTo = now;
            updateDates.run();
        });
        fromButton.setOnClickListener(v -> pickManualDate(true, updateDates));
        toButton.setOnClickListener(v -> pickManualDate(false, updateDates));

        form.addView(space(dp(16)));
        TextView sourceLabel = bodyText("사진 출처");
        sourceLabel.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        form.addView(sourceLabel);
        CheckBox apple = new CheckBox(this);
        apple.setText("Apple Photos");
        apple.setChecked(true);
        apple.setMinHeight(dp(48));
        CheckBox google = new CheckBox(this);
        google.setText("Google Photos");
        google.setChecked(true);
        google.setMinHeight(dp(48));
        form.addView(apple, matchWrap());
        form.addView(google, matchWrap());

        form.addView(space(dp(16)));
        TextView compositionLabel = bodyText("사진 구성");
        compositionLabel.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        form.addView(compositionLabel);
        RadioGroup selectionModes = new RadioGroup(this);
        selectionModes.setOrientation(LinearLayout.VERTICAL);
        selectionModes.setContentDescription("Story에 담을 사진 구성");
        RadioButton balanced = selectionModeButton(
                "균형 있게", "사람과 풍경을 고르게 담습니다.", SELECTION_MODE_BALANCED);
        RadioButton people = selectionModeButton(
                "인물 위주", "사람이 나온 사진을 우선합니다. Apple Photos에서만 지원합니다.",
                SELECTION_MODE_PEOPLE_PRESENT);
        RadioButton landscape = selectionModeButton(
                "풍경 위주", "사람보다 장소와 풍경을 우선합니다.", SELECTION_MODE_LANDSCAPE);
        selectionModes.addView(balanced, matchWrap());
        selectionModes.addView(people, matchWrap());
        selectionModes.addView(landscape, matchWrap());
        if (SELECTION_MODE_PEOPLE_PRESENT.equals(manualSelectionMode)) {
            selectionModes.check(people.getId());
        } else if (SELECTION_MODE_LANDSCAPE.equals(manualSelectionMode)) {
            selectionModes.check(landscape.getId());
        } else {
            selectionModes.check(balanced.getId());
        }
        form.addView(selectionModes, matchWrap());

        form.addView(space(dp(16)));
        TextView limitLabel = bodyText("최대 사진 수");
        limitLabel.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        EditText limitInput = new EditText(this);
        limitInput.setInputType(InputType.TYPE_CLASS_NUMBER);
        limitInput.setText("500");
        limitInput.setHint("1~1000");
        limitInput.setContentDescription("한 작업에서 분석할 최대 사진 수, 1장부터 1000장");
        form.addView(limitLabel);
        form.addView(limitInput, matchWrap());

        CheckBox reanalyze = new CheckBox(this);
        reanalyze.setText("해당 기간 전체 다시 분석");
        reanalyze.setMinHeight(dp(52));
        reanalyze.setContentDescription(
                "기존 분석 여부와 관계없이 선택한 날짜의 원본 사진을 다시 분석");
        form.addView(space(dp(12)));
        form.addView(reanalyze, matchWrap());

        TextView policy = bodyText(analysisPolicyText(false));
        policy.setTextColor(muted);
        form.addView(policy);
        page.addView(card(form));

        TextView previewText = bodyText("날짜와 출처를 정한 뒤 사진 수를 확인해 주세요.");
        page.addView(card(previewText));
        Button preview = quietButton("사진 수 미리보기");
        Button start = primaryButton("분석하고 Story 만들기");
        start.setEnabled(false);
        page.addView(preview, matchWrap());
        page.addView(space(dp(8)));
        page.addView(start, matchWrap());
        setScrollable(page);

        JSONObject[] approvedPayload = {null};
        int[] previewGeneration = {0};
        preview.setOnClickListener(v -> {
            JSONObject payload = manualPayload(
                    apple, google, selectionModes, limitInput, reanalyze, previewText);
            if (payload == null) return;
            int requestGeneration = ++previewGeneration[0];
            preview.setEnabled(false);
            start.setEnabled(false);
            previewText.setText("Mac mini의 사진 보관함을 확인하고 있어요…");
            setLoading(true);
            executor.execute(() -> {
                try {
                    JSONObject response = new OwnerApiClient(this).previewManualCuration(payload);
                    JSONObject data = response.getJSONObject("data");
                    JSONObject providers = data.getJSONObject("providers");
                    MediaDayCounter.Result local = MediaDayCounter.count(
                            this, manualDateFrom, manualDateTo);
                    StringBuilder summary = new StringBuilder();
                    if (providers.has("apple")) {
                        JSONObject value = providers.getJSONObject("apple");
                        summary.append("Apple Photos  ·  ").append(value.optInt("count")).append("장")
                                .append("\n기존 분석 ").append(value.optInt("reusable_count"))
                                .append("장 · 새 분석 ").append(value.optInt("new_analysis_count")).append("장");
                        if (payload.optBoolean("reanalyze", false)) {
                            summary.append(" · 재분석 ").append(value.optInt("reanalyze_count")).append("장");
                        }
                        summary.append("\n\n");
                    }
                    if (providers.has("google")) {
                        summary.append("Google Photos  ·  Picker 선택 후 확인\n\n");
                    }
                    summary.append("이 휴대폰 카메라 원본  ·  ").append(local.count).append("장")
                            .append("partial_permission".equals(local.status) ? " · 부분 조회"
                                    : "permission_required".equals(local.status)
                                    ? " · 사진 권한 필요" : "")
                            .append("\n시작하면 이 날짜 범위를 다시 읽어 GPS를 먼저 동기화합니다. "
                                    + "Apple·Google 수와 합산하지 않습니다.");
                    runOnUiThread(() -> {
                        if (requestGeneration != previewGeneration[0]) {
                            preview.setEnabled(true);
                            setLoading(false);
                            return;
                        }
                        approvedPayload[0] = payload;
                        previewText.setText(summary.toString());
                        preview.setEnabled(true);
                        start.setEnabled(true);
                        setLoading(false);
                    });
                } catch (Exception error) {
                    runOnUiThread(() -> {
                        if (requestGeneration != previewGeneration[0]) {
                            preview.setEnabled(true);
                            setLoading(false);
                            return;
                        }
                        previewText.setText(connectionHelp(error));
                        preview.setEnabled(true);
                        setLoading(false);
                    });
                }
            });
        });
        View.OnClickListener invalidatePreview = v -> {
            previewGeneration[0]++;
            approvedPayload[0] = null;
            start.setEnabled(false);
            if (SELECTION_MODE_PEOPLE_PRESENT.equals(selectedSelectionMode(selectionModes))
                    && google.isChecked()) {
                previewText.setText(peopleModeGoogleUnsupportedMessage());
            } else {
                previewText.setText("조건이 바뀌었습니다. 사진 수를 다시 확인해 주세요.");
            }
        };
        apple.setOnClickListener(invalidatePreview);
        google.setOnClickListener(invalidatePreview);
        reanalyze.setOnClickListener(v -> {
            policy.setText(analysisPolicyText(reanalyze.isChecked()));
            invalidatePreview.onClick(v);
        });
        selectionModes.setOnCheckedChangeListener((group, checkedId) -> {
            manualSelectionMode = selectedSelectionMode(group);
            invalidatePreview.onClick(group);
        });
        limitInput.setOnFocusChangeListener((v, hasFocus) -> {
            if (hasFocus) invalidatePreview.onClick(v);
        });
        start.setOnClickListener(v -> {
            if (approvedPayload[0] == null) return;
            JSONObject currentPayload = manualPayload(
                    apple, google, selectionModes, limitInput, reanalyze, previewText);
            if (currentPayload == null
                    || !currentPayload.toString().equals(approvedPayload[0].toString())) {
                approvedPayload[0] = null;
                start.setEnabled(false);
                previewText.setText("조건이 바뀌었습니다. 사진 수를 다시 확인해 주세요.");
                return;
            }
            String range = manualDateFrom.equals(manualDateTo)
                    ? manualDateFrom.toString()
                    : manualDateFrom + " ~ " + manualDateTo;
            new AlertDialog.Builder(this)
                    .setTitle("새 Story 작업을 시작할까요?")
                    .setMessage("촬영일 " + range + "\n"
                            + "출처 " + sourceNames(apple.isChecked(), google.isChecked()) + "\n"
                            + "사진 구성 " + selectionModeLabel(
                                    approvedPayload[0].optString("selection_mode")) + "\n"
                            + "최대 " + approvedPayload[0].optInt("limit") + "장 · 최대 6시간\n\n"
                            + "분석 전 선택 날짜의 휴대폰 원본 GPS를 먼저 동기화합니다.\n\n"
                            + analysisPolicyText(
                                    approvedPayload[0].optBoolean("reanalyze", false)))
                    .setNegativeButton("취소", null)
                    .setPositiveButton("분석 시작", (dialog, which) ->
                            submitManualCuration(approvedPayload[0], start, previewText))
                    .show();
        });
    }

    private void pickManualDate(boolean start, Runnable updated) {
        LocalDate initial = start ? manualDateFrom : manualDateTo;
        DatePickerDialog picker = new DatePickerDialog(
                this,
                (view, year, month, day) -> {
                    LocalDate picked = LocalDate.of(year, month + 1, day);
                    if (picked.isAfter(LocalDate.now(java.time.ZoneId.of("Asia/Seoul")))) return;
                    if (start) {
                        manualDateFrom = picked;
                        if (manualDateTo.isBefore(picked)) manualDateTo = picked;
                    } else {
                        manualDateTo = picked;
                        if (manualDateFrom.isAfter(picked)) manualDateFrom = picked;
                    }
                    if (java.time.temporal.ChronoUnit.DAYS.between(manualDateFrom, manualDateTo) > 30) {
                        if (start) manualDateTo = manualDateFrom.plusDays(30);
                        else manualDateFrom = manualDateTo.minusDays(30);
                    }
                    updated.run();
                },
                initial.getYear(), initial.getMonthValue() - 1, initial.getDayOfMonth());
        picker.getDatePicker().setMaxDate(System.currentTimeMillis());
        picker.show();
    }

    private JSONObject manualPayload(
            CheckBox apple, CheckBox google, RadioGroup selectionModes,
            EditText limitInput, CheckBox reanalyze, TextView status) {
        if (!apple.isChecked() && !google.isChecked()) {
            status.setText("Apple Photos 또는 Google Photos 중 하나 이상 선택해 주세요.");
            return null;
        }
        String selectionMode = selectedSelectionMode(selectionModes);
        if (SELECTION_MODE_PEOPLE_PRESENT.equals(selectionMode) && google.isChecked()) {
            status.setText(peopleModeGoogleUnsupportedMessage());
            return null;
        }
        int limit;
        try {
            limit = Integer.parseInt(limitInput.getText().toString().trim());
        } catch (NumberFormatException error) {
            limit = 0;
        }
        if (limit < 1 || limit > 1000) {
            status.setText("최대 사진 수는 1장부터 1000장까지 입력해 주세요.");
            return null;
        }
        if (apple.isChecked() && google.isChecked() && limit < 2) {
            status.setText("두 출처를 함께 분석하려면 최대 사진 수를 2장 이상으로 입력해 주세요.");
            return null;
        }
        try {
            JSONArray sources = new JSONArray();
            int selected = 0;
            if (apple.isChecked()) { sources.put("apple"); selected++; }
            if (google.isChecked()) { sources.put("google"); selected++; }
            JSONObject providerLimits = new JSONObject();
            if (selected == 2) {
                providerLimits.put("apple", limit / 2);
                providerLimits.put("google", limit - limit / 2);
            } else if (apple.isChecked()) providerLimits.put("apple", limit);
            else providerLimits.put("google", limit);
            JSONObject payload = new JSONObject();
            payload.put("schema_version", 1);
            payload.put("date_from", manualDateFrom.toString());
            payload.put("date_to", manualDateTo.toString());
            payload.put("timezone", "Asia/Seoul");
            payload.put("sources", sources);
            payload.put("limit", limit);
            payload.put("selection_mode", selectionMode);
            payload.put("provider_limits", providerLimits);
            payload.put("exclude_screenshots", true);
            payload.put("timeout_seconds", 21600);
            payload.put("reanalyze", reanalyze.isChecked());
            payload.put("publication_policy", "none");
            payload.put("story_policy", "run_scoped");
            return payload;
        } catch (Exception error) {
            status.setText("작업 조건을 준비하지 못했습니다.");
            return null;
        }
    }

    private static String analysisPolicyText(boolean reanalyze) {
        if (reanalyze) {
            return "선택한 기간의 원본 사진을 기존 분석 여부와 관계없이 다시 분석합니다. "
                    + "Photos MCP 결과 앨범 사본과 화면 캡처는 제외하며, 자동 앨범은 변경하지 않습니다.";
        }
        return "기존 분석 결과는 재사용하고 새 사진만 분석합니다. 최대 6시간이며, "
                + "자동 앨범에는 추가하지 않습니다.";
    }

    private String sourceNames(boolean apple, boolean google) {
        if (apple && google) return "Apple Photos + Google Photos";
        return apple ? "Apple Photos" : "Google Photos";
    }

    private RadioButton selectionModeButton(String title, String description, String value) {
        RadioButton button = new RadioButton(this);
        button.setId(View.generateViewId());
        button.setTag(value);
        button.setText(title + "\n" + description);
        button.setTextColor(ink);
        button.setTextSize(15);
        button.setMinHeight(dp(56));
        button.setContentDescription(title + ". " + description);
        return button;
    }

    private String selectedSelectionMode(RadioGroup group) {
        View selected = group.findViewById(group.getCheckedRadioButtonId());
        return normalizedSelectionMode(selected == null ? null : String.valueOf(selected.getTag()));
    }

    private static String normalizedSelectionMode(String value) {
        if (SELECTION_MODE_PEOPLE_PRESENT.equals(value)
                || SELECTION_MODE_LANDSCAPE.equals(value)) {
            return value;
        }
        return SELECTION_MODE_BALANCED;
    }

    private static String selectionModeLabel(String value) {
        if (SELECTION_MODE_PEOPLE_PRESENT.equals(value)) return "인물 위주";
        if (SELECTION_MODE_LANDSCAPE.equals(value)) return "풍경 위주";
        return "균형 있게";
    }

    private static String peopleModeGoogleUnsupportedMessage() {
        return "인물 위주는 현재 Apple Photos만 지원합니다. "
                + "Google Photos를 해제하거나 다른 사진 구성을 선택해 주세요.";
    }

    private void submitManualCuration(JSONObject payload, Button start, TextView status) {
        if (!hasMediaPermissions()) {
            requestMediaPermissions();
            status.setText("선택한 날짜의 원본 GPS를 읽으려면 사진·원본 위치 권한이 필요합니다. "
                    + "권한을 허용한 뒤 다시 시작해 주세요.");
            return;
        }
        start.setEnabled(false);
        status.setText("선택한 날짜의 휴대폰 원본 GPS를 먼저 동기화하고 있어요…");
        setLoading(true);
        executor.execute(() -> {
            try {
                LocalDate from = LocalDate.parse(payload.getString("date_from"));
                LocalDate to = LocalDate.parse(payload.getString("date_to"));
                BridgeSync.Result locationSync = BridgeSync.runRange(this, from, to);
                if (locationSync.remaining != 0) {
                    throw new IllegalStateException(
                            "GPS 전송 대기 배치 " + locationSync.remaining + "개가 남아 있습니다");
                }
                payload.put("location_prefetch", locationPrefetch(from, to, locationSync));
                runOnUiThread(() -> status.setText(
                        "카메라 원본 " + locationSync.scanned + "장 중 GPS "
                                + locationSync.queued
                                + "건 확인 완료 · Mac mini에 분석 작업을 등록하고 있어요…"));
                JSONObject operation = new OwnerApiClient(this)
                        .startManualCuration(payload).getJSONObject("data");
                String operationId = operation.getString("operation_id");
                runOnUiThread(() -> showManualOperation(operationId));
            } catch (SecurityException denied) {
                runOnUiThread(() -> {
                    status.setText("선택한 날짜의 원본 GPS를 읽지 못해 분석을 시작하지 않았습니다. "
                            + "설정에서 사진·원본 위치 권한을 허용해 주세요.");
                    start.setEnabled(true);
                    setLoading(false);
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    status.setText("GPS 동기화를 완료하지 못해 분석을 시작하지 않았습니다.\n"
                            + connectionHelp(error));
                    start.setEnabled(true);
                    setLoading(false);
                });
            }
        });
    }

    private JSONObject locationPrefetch(
            LocalDate from, LocalDate to, BridgeSync.Result locationSync) throws Exception {
        JSONObject receipt = new JSONObject();
        receipt.put("schema_version", 1);
        receipt.put("status", "completed");
        receipt.put("date_from", from.toString());
        receipt.put("date_to", to.toString());
        receipt.put("timezone", "Asia/Seoul");
        receipt.put("scanned_count", Math.max(0, locationSync.scanned));
        receipt.put("gps_manifest_count", Math.max(0, locationSync.queued));
        receipt.put("remaining_batches", Math.max(0, locationSync.remaining));
        receipt.put("extractor_version", "android-bridge-2");
        String clientVersion = getPackageManager()
                .getPackageInfo(getPackageName(), 0).versionName;
        receipt.put("client_version", clientVersion == null ? "0.7.3" : clientVersion);
        receipt.put("completed_at", Instant.now().toString());
        return receipt;
    }

    private void showManualOperation(String operationId) {
        selectSection("runs");
        int generation = manualPollingGeneration;
        LinearLayout page = page("직접 실행 작업", "앱을 닫아도 Mac mini에서 계속 진행됩니다.");
        TextView status = bodyText("작업 상태를 확인하고 있어요…");
        LinearLayout failureContent = new LinearLayout(this);
        failureContent.setOrientation(LinearLayout.VERTICAL);
        View failureCard = card(failureContent);
        failureCard.setVisibility(View.GONE);
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.VERTICAL);
        page.addView(card(status));
        page.addView(failureCard);
        page.addView(actions);
        setScrollable(page);
        pollManualOperation(
                operationId, status, failureContent, failureCard, actions, generation);
    }

    private void pollManualOperation(
            String operationId, TextView statusView, LinearLayout failureContent,
            View failureCard, LinearLayout actions, int generation) {
        if (!canPollManualOperation(generation)) return;
        setLoading(true);
        try {
            executor.execute(() -> {
            try {
                JSONObject item = new OwnerApiClient(this)
                        .getManualOperation(operationId).getJSONObject("data");
                String state = item.optString("status");
                boolean reanalysis = item.optBoolean("reanalyze", false);
                StringBuilder copy = new StringBuilder();
                copy.append(statusLabel(state))
                        .append(reanalysis ? " · 재분석\n" : " · 직접 실행\n")
                        .append(item.optString("date_from"));
                if (!item.optString("date_from").equals(item.optString("date_to"))) {
                    copy.append(" ~ ").append(item.optString("date_to"));
                }
                if (item.optInt("queue_position") > 0) {
                    copy.append("\n대기 순서 ").append(item.optInt("queue_position")).append("번째");
                }
                copy.append("\n분석 ").append(item.optInt("processed_count")).append("장")
                        .append(" · 추천 ").append(item.optInt("recommended_count")).append("장");
                String storyId = item.optString("story_id");
                boolean terminal = "completed".equals(state) || "completed_empty".equals(state)
                        || "partial".equals(state) || "partial_timeout".equals(state)
                        || "failed".equals(state) || "cancelled".equals(state);
                String failureDetail = operationFailureDetail(item, operationId, true);
                runOnUiThread(() -> {
                    if (!canPollManualOperation(generation)) return;
                    statusView.setText(copy.toString());
                    renderOperationFailure(failureContent, failureCard, failureDetail);
                    actions.removeAllViews();
                    if (!storyId.isEmpty()) {
                        Button story = primaryButton("Story 보기");
                        story.setOnClickListener(v -> openStory(storyId));
                        Button results = quietButton("추천 사진 보기");
                        results.setOnClickListener(v -> showResults(storyId));
                        actions.addView(story, matchWrap());
                        actions.addView(space(dp(8)));
                        actions.addView(results, matchWrap());
                    } else if ("completed_empty".equals(state)) {
                        actions.addView(bodyText("선택한 날짜에 Story로 만들 추천 사진이 없습니다."));
                        Button change = quietButton("날짜 바꾸기");
                        change.setOnClickListener(v -> showManualCuration());
                        actions.addView(change, matchWrap());
                    } else if ("failed".equals(state) || "interrupted".equals(state)) {
                        Button retry = quietButton("조건을 확인하고 다시 실행");
                        retry.setContentDescription("실패 원인을 확인한 뒤 새 사진 분석 작업 설정하기");
                        retry.setOnClickListener(v -> showManualCuration());
                        actions.addView(retry, matchWrap());
                    }
                    setLoading(false);
                    if (!terminal) {
                        scheduledManualPoll = () -> {
                            scheduledManualPoll = null;
                            pollManualOperation(
                                    operationId, statusView, failureContent,
                                    failureCard, actions, generation);
                        };
                        manualPollingHandler.postDelayed(scheduledManualPoll, 5000L);
                    }
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (!canPollManualOperation(generation)) return;
                    statusView.setText(connectionHelp(error));
                    setLoading(false);
                });
            }
            });
        } catch (RejectedExecutionException ignored) {
            // A delayed poll may race Activity teardown. Teardown owns the executor.
        }
    }

    private void showResults() {
        showResults("");
    }

    private void showResults(String storyId) {
        selectSection("results");
        resultsStoryId = storyId == null ? "" : storyId;
        int generation = ++resultsGeneration;
        LinearLayout page = page("추천 사진", "Apple Photos와 Google Photos에서 고른 사진을 한곳에서 봅니다.");
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        TextView loading = bodyText("추천 사진을 불러오는 중…");
        content.addView(card(loading));
        page.addView(content);
        setScrollable(page);
        if (!new OwnerApiClient(this).canConnect()) {
            loading.setText("설정에서 휴대폰을 먼저 등록해 주세요.");
            return;
        }
        setLoading(true);
        executor.execute(() -> {
            try {
                JSONObject response = new OwnerApiClient(this).getResults(resultsStoryId);
                JSONArray items = response.getJSONArray("data");
                runOnUiThread(() -> renderRecommendationGrid(content, items, generation));
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (generation != resultsGeneration || !"results".equals(currentSection)) return;
                    if (error instanceof OwnerApiClient.OwnerApiException
                            && ((OwnerApiClient.OwnerApiException) error).status == 404) {
                        renderRecommendationGrid(content, new JSONArray(), generation);
                        return;
                    }
                    loading.setText(connectionHelp(error));
                    setLoading(false);
                });
            }
        });
    }

    private void renderRecommendationGrid(
            LinearLayout content, JSONArray items, int generation) {
        if (generation != resultsGeneration || !"results".equals(currentSection)) return;
        content.removeAllViews();
        if (items.length() == 0) {
            content.addView(card(bodyText("아직 표시할 추천 사진이 없습니다.")));
            setLoading(false);
            return;
        }
        TextView summary = text(
                getString(R.string.recommendation_count_summary, items.length()), 14, muted);
        summary.setPadding(dp(2), 0, 0, dp(12));
        summary.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        content.addView(summary, matchWrap());

        int screenWidth = getResources().getDisplayMetrics().widthPixels;
        int usableWidth = Math.max(dp(280), screenWidth - dp(40));
        int columns = Math.max(2, Math.min(4, usableWidth / dp(170)));
        int gap = dp(8);
        int tileSize = Math.max(dp(132), (usableWidth - gap * columns) / columns);
        GridLayout grid = new GridLayout(this);
        grid.setColumnCount(columns);
        grid.setAlignmentMode(GridLayout.ALIGN_BOUNDS);
        grid.setUseDefaultMargins(false);
        grid.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
        for (int index = 0; index < items.length(); index++) {
            JSONObject item = items.optJSONObject(index);
            if (item == null) continue;
            View tile = recommendationTile(item, items, index, tileSize, generation);
            GridLayout.LayoutParams params = new GridLayout.LayoutParams();
            params.width = tileSize;
            params.height = tileSize;
            params.setMargins(gap / 2, gap / 2, gap / 2, gap / 2);
            grid.addView(tile, params);
        }
        content.addView(grid, matchWrap());
        setLoading(false);
    }

    private View recommendationTile(
            JSONObject item, JSONArray allItems, int index, int tileSize, int generation) {
        FrameLayout tile = new FrameLayout(this);
        tile.setBackground(roundedBackground(softAccent, dp(14), 0, Color.TRANSPARENT));
        tile.setClipToOutline(true);
        tile.setClickable(true);
        tile.setFocusable(true);
        tile.setForeground(rippleBackground(Color.TRANSPARENT, dp(14)));
        tile.setContentDescription(resultSummary(item).replace('\n', ' ') + ", 크게 보기");
        tile.setOnClickListener(ignored -> showRecommendationViewer(allItems, index));

        ImageView placeholder = new ImageView(this);
        placeholder.setImageResource(R.drawable.ic_image_placeholder);
        placeholder.setColorFilter(muted);
        placeholder.setPadding(dp(42), dp(42), dp(42), dp(42));
        tile.addView(placeholder, new FrameLayout.LayoutParams(-1, -1));

        ImageView image = new ImageView(this);
        image.setScaleType(ImageView.ScaleType.CENTER_CROP);
        image.setVisibility(View.INVISIBLE);
        image.setContentDescription(null);
        tile.addView(image, new FrameLayout.LayoutParams(-1, -1));

        ProgressBar loading = new ProgressBar(this);
        loading.setIndeterminateTintList(ColorStateList.valueOf(accent));
        FrameLayout.LayoutParams loadingParams = new FrameLayout.LayoutParams(
                dp(36), dp(36), Gravity.CENTER);
        tile.addView(loading, loadingParams);

        GradientDrawable fade = new GradientDrawable(
                GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{Color.TRANSPARENT, Color.argb(210, 10, 16, 13)});
        View scrim = new View(this);
        scrim.setBackground(fade);
        FrameLayout.LayoutParams scrimParams = new FrameLayout.LayoutParams(-1, dp(76), Gravity.BOTTOM);
        tile.addView(scrim, scrimParams);

        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.VERTICAL);
        labels.setPadding(dp(10), dp(8), dp(10), dp(9));
        TextView title = text(resultTitle(item), 14, Color.WHITE);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        title.setSingleLine(true);
        title.setEllipsize(TextUtils.TruncateAt.END);
        TextView detail = text(resultCompactDetail(item), 12, Color.rgb(225, 232, 228));
        detail.setSingleLine(true);
        detail.setEllipsize(TextUtils.TruncateAt.END);
        labels.addView(title, matchWrap());
        labels.addView(detail, matchWrap());
        tile.addView(labels, new FrameLayout.LayoutParams(-1, -2, Gravity.BOTTOM));

        String assetId = item.optString("asset_id");
        imageExecutor.execute(() -> {
            try {
                Bitmap bitmap = imageCache.load(
                        new OwnerApiClient(this), assetId, "thumb", tileSize, resultsStoryId);
                runOnUiThread(() -> {
                    if (generation != resultsGeneration || !"results".equals(currentSection)) return;
                    image.setImageBitmap(bitmap);
                    image.setVisibility(View.VISIBLE);
                    placeholder.setVisibility(View.GONE);
                    loading.setVisibility(View.GONE);
                });
            } catch (Exception ignored) {
                runOnUiThread(() -> {
                    if (generation != resultsGeneration || !"results".equals(currentSection)) return;
                    loading.setVisibility(View.GONE);
                    placeholder.setContentDescription("미리보기를 불러오지 못했습니다");
                });
            }
        });
        return tile;
    }

    private void showRecommendationViewer(JSONArray items, int selectedIndex) {
        if (recommendationViewer != null && recommendationViewer.isShowing()) {
            recommendationViewer.dismiss();
        }
        Dialog dialog = new Dialog(this, android.R.style.Theme_DeviceDefault_NoActionBar);
        recommendationViewer = dialog;
        int[] position = {Math.max(0, Math.min(selectedIndex, items.length() - 1))};

        LinearLayout shell = new LinearLayout(this);
        shell.setOrientation(LinearLayout.VERTICAL);
        shell.setBackgroundColor(Color.rgb(8, 13, 11));
        shell.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(
                        WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else {
                view.setPadding(
                        insets.getSystemWindowInsetLeft(),
                        insets.getSystemWindowInsetTop(),
                        insets.getSystemWindowInsetRight(),
                        insets.getSystemWindowInsetBottom());
            }
            return insets;
        });

        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.END | Gravity.CENTER_VERTICAL);
        top.setPadding(dp(8), dp(8), dp(8), dp(8));
        top.addView(new View(this), new LinearLayout.LayoutParams(0, dp(48), 1));
        Button zoomReset = viewerZoomAction("1×", "원래 크기");
        top.addView(zoomReset, new LinearLayout.LayoutParams(dp(48), dp(48)));
        ImageButton close = viewerAction(R.drawable.ic_close, "큰 사진 닫기");
        close.setOnClickListener(ignored -> dialog.dismiss());
        top.addView(close, new LinearLayout.LayoutParams(dp(48), dp(48)));
        shell.addView(top, new LinearLayout.LayoutParams(-1, dp(64)));

        FrameLayout stage = new FrameLayout(this);
        ZoomableImageView image = new ZoomableImageView(this);
        image.setContentDescription("추천 사진 크게 보기");
        stage.addView(image, new FrameLayout.LayoutParams(-1, -1));
        ProgressBar loading = new ProgressBar(this);
        loading.setIndeterminateTintList(ColorStateList.valueOf(Color.WHITE));
        stage.addView(loading, new FrameLayout.LayoutParams(dp(44), dp(44), Gravity.CENTER));
        TextView failure = text("사진을 불러오지 못했어요", 15, Color.LTGRAY);
        failure.setGravity(Gravity.CENTER);
        failure.setVisibility(View.GONE);
        stage.addView(failure, new FrameLayout.LayoutParams(-1, -1));

        shell.addView(stage, new LinearLayout.LayoutParams(-1, 0, 1));

        LinearLayout positionIndicator = new LinearLayout(this);
        positionIndicator.setOrientation(LinearLayout.VERTICAL);
        positionIndicator.setPadding(dp(20), dp(10), dp(20), 0);
        TextView count = text("", 13, Color.WHITE);
        count.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        count.setGravity(Gravity.CENTER);
        count.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        positionIndicator.addView(count, new LinearLayout.LayoutParams(-1, dp(24)));
        ProgressBar positionProgress = new ProgressBar(
                this, null, android.R.attr.progressBarStyleHorizontal);
        positionProgress.setIndeterminate(false);
        positionProgress.setMax(Math.max(1, items.length()));
        positionProgress.setProgressTintList(
                ColorStateList.valueOf(Color.rgb(130, 207, 180)));
        positionProgress.setProgressBackgroundTintList(
                ColorStateList.valueOf(Color.argb(72, 255, 255, 255)));
        positionProgress.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        LinearLayout.LayoutParams progressParams = new LinearLayout.LayoutParams(-1, dp(3));
        progressParams.setMargins(0, dp(4), 0, 0);
        positionIndicator.addView(positionProgress, progressParams);
        shell.addView(positionIndicator, new LinearLayout.LayoutParams(-1, dp(41)));

        TextView gestureHint = text(
                "두 번 탭하거나 두 손가락으로 확대 · 기본 크기에서 좌우로 넘기기",
                12,
                Color.rgb(190, 202, 196));
        gestureHint.setGravity(Gravity.CENTER);
        gestureHint.setPadding(dp(16), dp(8), dp(16), 0);
        shell.addView(gestureHint, new LinearLayout.LayoutParams(-1, -2));

        TextView detail = text("", 15, Color.WHITE);
        detail.setPadding(dp(20), dp(14), dp(20), dp(18));
        detail.setLineSpacing(0, 1.2f);
        detail.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        shell.addView(detail, new LinearLayout.LayoutParams(-1, -2));

        Runnable[] refresh = new Runnable[1];
        refresh[0] = () -> {
            JSONObject item = items.optJSONObject(position[0]);
            if (item == null) return;
            int loadGeneration = ++viewerLoadGeneration;
            count.setText(getString(
                    R.string.recommendation_viewer_count, position[0] + 1, items.length()));
            positionProgress.setProgress(position[0] + 1, true);
            detail.setText(resultSummary(item));
            image.setContentDescription(resultSummary(item).replace('\n', ' '));
            image.setImageDrawable(null);
            loading.setVisibility(View.VISIBLE);
            failure.setVisibility(View.GONE);
            String assetId = item.optString("asset_id");
            int target = Math.max(
                    getResources().getDisplayMetrics().widthPixels,
                    getResources().getDisplayMetrics().heightPixels);
            imageExecutor.execute(() -> {
                try {
                    Bitmap bitmap = imageCache.load(
                            new OwnerApiClient(this), assetId, "preview", target, resultsStoryId);
                    runOnUiThread(() -> {
                        if (loadGeneration != viewerLoadGeneration || !dialog.isShowing()) return;
                        image.setImageBitmap(bitmap);
                        loading.setVisibility(View.GONE);
                    });
                } catch (Exception ignored) {
                    runOnUiThread(() -> {
                        if (loadGeneration != viewerLoadGeneration || !dialog.isShowing()) return;
                        loading.setVisibility(View.GONE);
                        failure.setVisibility(View.VISIBLE);
                    });
                }
            });
        };
        image.setNavigationListener(new ZoomableImageView.NavigationListener() {
            @Override public void onNavigateRequested(int offset) {
                position[0] = (position[0] + offset + items.length()) % items.length();
                refresh[0].run();
            }

            @Override public void onZoomChanged(float scale) {
                zoomReset.setText(String.format(java.util.Locale.ROOT, "%.1f×", scale));
                zoomReset.setContentDescription(String.format(
                        java.util.Locale.ROOT, "현재 %.1f배, 원래 크기로", scale));
            }
        });
        zoomReset.setOnClickListener(ignored -> image.resetZoom());
        dialog.setOnDismissListener(ignored -> {
            viewerLoadGeneration++;
            if (recommendationViewer == dialog) recommendationViewer = null;
        });
        dialog.setContentView(shell);
        dialog.show();
        Window window = dialog.getWindow();
        if (window != null) {
            window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_FULLSCREEN);
            window.addFlags(android.view.WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
            window.setBackgroundDrawable(new ColorDrawable(Color.rgb(8, 13, 11)));
            window.setStatusBarColor(Color.rgb(8, 13, 11));
            window.setNavigationBarColor(Color.rgb(8, 13, 11));
            if (Build.VERSION.SDK_INT >= 28) {
                window.setNavigationBarDividerColor(Color.rgb(8, 13, 11));
            }
            View decor = window.getDecorView();
            if (Build.VERSION.SDK_INT >= 30) {
                window.setDecorFitsSystemWindows(false);
                android.view.WindowInsetsController controller = window.getInsetsController();
                if (controller != null) {
                    controller.show(
                            WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                    controller.setSystemBarsAppearance(
                            0,
                            android.view.WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                                    | android.view.WindowInsetsController
                                            .APPEARANCE_LIGHT_NAVIGATION_BARS);
                }
            } else {
                int visibility = decor.getSystemUiVisibility();
                visibility &= ~(
                        View.SYSTEM_UI_FLAG_FULLSCREEN
                                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                                | View.SYSTEM_UI_FLAG_IMMERSIVE
                                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                                | View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
                                | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
                decor.setSystemUiVisibility(visibility);
            }
            window.setLayout(-1, -1);
            decor.requestApplyInsets();
        }
        refresh[0].run();
    }

    private ImageButton viewerAction(int drawable, String description) {
        ImageButton button = new ImageButton(this);
        button.setImageResource(drawable);
        button.setColorFilter(Color.WHITE);
        button.setPadding(dp(14), dp(14), dp(14), dp(14));
        button.setBackground(rippleBackground(Color.argb(74, 255, 255, 255), dp(28)));
        button.setContentDescription(description);
        button.setFocusable(true);
        return button;
    }

    private Button viewerZoomAction(String label, String description) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(16);
        button.setTextColor(Color.WHITE);
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setPadding(0, 0, 0, 0);
        button.setBackground(rippleBackground(Color.argb(74, 255, 255, 255), dp(24)));
        button.setContentDescription(description);
        button.setFocusable(true);
        return button;
    }

    private void loadJsonList(String kind, LinearLayout list) {
        TextView loading = bodyText("불러오는 중…");
        list.addView(card(loading));
        if (!new OwnerApiClient(this).canConnect()) {
            loading.setText("설정에서 휴대폰을 먼저 등록해 주세요.");
            return;
        }
        setLoading(true);
        executor.execute(() -> {
            try {
                JSONObject response = "runs".equals(kind)
                        ? new OwnerApiClient(this).getRuns()
                        : new OwnerApiClient(this).getResults();
                JSONArray items = response.getJSONArray("data");
                runOnUiThread(() -> {
                    list.removeAllViews();
                    if (items.length() == 0) {
                        list.addView(card(bodyText("아직 표시할 내용이 없습니다.")));
                    }
                    for (int i = 0; i < items.length(); i++) {
                        JSONObject item = items.optJSONObject(i);
                        if (item == null) continue;
                        list.addView(card(bodyText(
                                "runs".equals(kind) ? runSummary(item) : resultSummary(item))));
                    }
                    setLoading(false);
                });
            } catch (Exception error) {
                runOnUiThread(() -> { loading.setText(connectionHelp(error)); setLoading(false); });
            }
        });
    }

    private void showStory() {
        openStory("");
    }

    private void showStories() {
        selectSection("story");
        int generation = ++resultsGeneration;
        LinearLayout page = page("사진 이야기", "자동 및 직접 실행으로 만든 Story를 날짜별로 엽니다.");
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        TextView loading = bodyText("Story 목록을 불러오는 중…");
        list.addView(card(loading));
        page.addView(list);
        setScrollable(page);
        if (!new OwnerApiClient(this).canConnect()) {
            loading.setText("설정에서 휴대폰을 먼저 등록해 주세요.");
            return;
        }
        setLoading(true);
        executor.execute(() -> {
            try {
                JSONArray items = new OwnerApiClient(this).getStories().getJSONArray("data");
                runOnUiThread(() -> {
                    if (generation != resultsGeneration || !"story".equals(currentSection)) return;
                    list.removeAllViews();
                    if (items.length() == 0) {
                        list.addView(card(bodyText("아직 표시할 Story가 없습니다.")));
                    }
                    for (int index = 0; index < items.length(); index++) {
                        JSONObject item = items.optJSONObject(index);
                        if (item == null) continue;
                        String storyId = item.optString("story_id");
                        LinearLayout entry = new LinearLayout(this);
                        entry.setOrientation(LinearLayout.VERTICAL);
                        String title = item.optString("title", "사진 이야기");
                        String range = item.optString("date_from");
                        String to = item.optString("date_to");
                        if (!to.isBlank() && !to.equals(range)) range += " ~ " + to;
                        String displayRange = range;
                        String origin = "manual".equals(item.optString("origin"))
                                ? "직접 실행" : "자동 정리";
                        String peopleCaption = storyPeopleCaption(item);
                        entry.addView(bodyText(title + "\n" + range + " · " + origin
                                + " · " + item.optInt("photo_count") + "장"
                                + (peopleCaption.isEmpty() ? "" : "\n" + peopleCaption)));
                        Button open = primaryButton("Story 보기");
                        open.setOnClickListener(v -> openStory(storyId));
                        Button photos = quietButton("추천 사진 보기");
                        photos.setOnClickListener(v -> showResults(storyId));
                        entry.addView(open, matchWrap());
                        entry.addView(space(dp(6)));
                        entry.addView(photos, matchWrap());
                        entry.addView(space(dp(6)));
                        if ("manual".equals(item.optString("origin"))) {
                            Button reanalyze = quietButton("같은 기간 전체 재분석");
                            reanalyze.setContentDescription(
                                    title + "을 만든 날짜 범위의 원본 전체를 다시 분석");
                            reanalyze.setOnClickListener(v ->
                                    confirmStoryReanalysis(
                                            storyId,
                                            title,
                                            item.optString(
                                                    "analysis_date_from",
                                                    item.optString("date_from")),
                                            item.optString(
                                                    "analysis_date_to",
                                                    item.optString("date_to")),
                                            displayRange));
                            entry.addView(reanalyze, matchWrap());
                            entry.addView(space(dp(6)));
                        }
                        Button delete = dangerButton("Story 삭제");
                        delete.setContentDescription(title + " Story 삭제");
                        delete.setOnClickListener(v -> confirmStoryDeletion(storyId, title));
                        entry.addView(delete, matchWrap());
                        list.addView(card(entry));
                    }
                    setLoading(false);
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    loading.setText(connectionHelp(error));
                    setLoading(false);
                });
            }
        });
    }

    private void confirmStoryReanalysis(
            String storyId, String title, String dateFrom, String dateTo, String range) {
        new AlertDialog.Builder(this)
                .setTitle("같은 기간의 원본 전체를 다시 분석할까요?")
                .setMessage(title + "\n" + range + "\n\n"
                        + "변경: 처음 사용한 날짜·출처·사진 구성·최대 장수로 새 분석과 Story를 만듭니다. "
                        + "선택 기간의 휴대폰 원본 GPS를 먼저 다시 동기화하며, "
                        + "Google Photos가 포함되면 Picker 선택을 다시 진행할 수 있습니다.\n\n"
                        + "유지: 기존 Story, 원본, 추천 보관소, 인물 이름·동의, GPS 정보와 추천 앨범은 유지됩니다.\n\n"
                        + "되돌리기: 새 작업이 실패하거나 취소되면 기존 Story는 그대로 남습니다.")
                .setNegativeButton("취소", null)
                .setPositiveButton("전체 재분석 시작", (dialog, which) -> {
                    if (!hasMediaPermissions()) {
                        requestMediaPermissions();
                        showMessage(
                                "사진 권한이 필요해요",
                                "같은 기간의 원본 GPS를 먼저 동기화하려면 사진·원본 위치 권한을 "
                                        + "모두 허용한 뒤 다시 실행해 주세요.");
                        return;
                    }
                    setLoading(true);
                    executor.execute(() -> {
                        try {
                            LocalDate from = LocalDate.parse(dateFrom);
                            LocalDate to = LocalDate.parse(
                                    dateTo == null || dateTo.isBlank() ? dateFrom : dateTo);
                            BridgeSync.Result locationSync = BridgeSync.runRange(this, from, to);
                            if (locationSync.remaining != 0) {
                                throw new IllegalStateException(
                                        "GPS 전송 대기 배치 " + locationSync.remaining
                                                + "개가 남아 있습니다");
                            }
                            JSONObject operation = new OwnerApiClient(this)
                                    .reanalyzeStory(
                                            storyId,
                                            locationPrefetch(from, to, locationSync))
                                    .getJSONObject("data");
                            String operationId = operation.getString("operation_id");
                            runOnUiThread(() -> showManualOperation(operationId));
                        } catch (Exception error) {
                            runOnUiThread(() -> {
                                setLoading(false);
                                showMessage("재분석을 시작하지 못했어요", connectionHelp(error));
                            });
                        }
                    });
                })
                .show();
    }

    private void confirmStoryDeletion(String storyId, String title) {
        new AlertDialog.Builder(this)
                .setTitle("Story를 삭제할까요?")
                .setMessage(title + "\n\nStory와 공유 링크만 제거됩니다. "
                        + "원본 사진, 추천 사진 사본, 분석 이력과 사진 앨범은 유지됩니다.")
                .setNegativeButton("취소", null)
                .setPositiveButton("Story 삭제", (dialog, which) -> {
                    setLoading(true);
                    executor.execute(() -> {
                        try {
                            new OwnerApiClient(this).deleteStory(storyId);
                            runOnUiThread(this::showStories);
                        } catch (Exception error) {
                            runOnUiThread(() -> {
                                setLoading(false);
                                showMessage("Story를 삭제하지 못했어요", connectionHelp(error));
                            });
                        }
                    });
                })
                .show();
    }

    private void showMessage(String title, String message) {
        new AlertDialog.Builder(this)
                .setTitle(title)
                .setMessage(message)
                .setPositiveButton("확인", null)
                .show();
    }

    private void openStory(String storyId) {
        selectSection("story");
        setLoading(true);
        TextView waiting = bodyText("사진 이야기를 안전하게 여는 중…");
        LinearLayout holder = page("사진 이야기", "추천 사진을 날짜와 장소별 이야기로 감상합니다.");
        holder.addView(card(waiting));
        setScrollable(holder);
        executor.execute(() -> {
            try {
                OwnerApiClient api = new OwnerApiClient(this);
                OwnerApiClient.WebBootstrap bootstrap = api.createWebBootstrap(storyId);
                String storyOrigin = api.storyOrigin();
                runOnUiThread(() -> openStoryWebView(bootstrap, storyOrigin));
            } catch (Exception error) {
                runOnUiThread(() -> { waiting.setText(connectionHelp(error)); setLoading(false); });
            }
        });
    }

    private void showInbox() {
        selectSection("inbox");
        LinearLayout page = page("알림", "완료, 일부 완료, 오류와 확인할 작업을 한곳에 모읍니다.");
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        TextView loading = bodyText("불러오는 중…");
        list.addView(card(loading));
        page.addView(list);
        setScrollable(page);
        if (!new OwnerApiClient(this).canConnect()) {
            loading.setText("설정에서 휴대폰을 먼저 등록해 주세요.");
            return;
        }
        setLoading(true);
        executor.execute(() -> {
            try {
                JSONArray events = new OwnerApiClient(this).getEvents().getJSONArray("data");
                runOnUiThread(() -> {
                    list.removeAllViews();
                    if (events.length() == 0) {
                        list.addView(card(bodyText("새 작업 알림이 없습니다.")));
                    }
                    for (int i = 0; i < events.length(); i++) {
                        JSONObject event = events.optJSONObject(i);
                        if (event == null) continue;
                        LinearLayout eventView = new LinearLayout(this);
                        eventView.setOrientation(LinearLayout.VERTICAL);
                        eventView.addView(bodyText(eventSummary(event)));
                        if (!event.optBoolean("acknowledged")) {
                            Button acknowledge = quietButton("확인함");
                            String eventId = event.optString("event_id");
                            acknowledge.setOnClickListener(v -> acknowledgeEvent(eventId, acknowledge));
                            eventView.addView(acknowledge);
                        }
                        list.addView(card(eventView));
                    }
                    setLoading(false);
                });
            } catch (Exception error) {
                runOnUiThread(() -> { loading.setText(connectionHelp(error)); setLoading(false); });
            }
        });
    }

    private void acknowledgeEvent(String eventId, Button button) {
        button.setEnabled(false);
        executor.execute(() -> {
            try {
                new OwnerApiClient(this).acknowledgeEvent(eventId);
                runOnUiThread(() -> { button.setText("확인됨"); button.setVisibility(View.GONE); });
            } catch (Exception error) {
                runOnUiThread(() -> { button.setText("다시 시도"); button.setEnabled(true); });
            }
        });
    }

    private void openStoryWebView(OwnerApiClient.WebBootstrap bootstrap, String allowedOrigin) {
        WebView web = new WebView(this);
        storyWebView = web;
        web.setBackgroundColor(paper);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setContentDescription("사진 이야기");
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setDatabaseEnabled(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        if (Build.VERSION.SDK_INT >= 26) settings.setSafeBrowsingEnabled(true);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(
                    WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (isAllowedStoryUri(uri, Uri.parse(allowedOrigin))) return false;
                if ("https".equals(uri.getScheme())) {
                    startActivity(new Intent(Intent.ACTION_VIEW, uri));
                }
                return true;
            }

            @Override public void onPageFinished(WebView view, String url) {
                String theme = isDarkMode() ? "dark" : "light";
                view.evaluateJavascript(
                        "document.documentElement.dataset.theme='" + theme + "'", null);
                setLoading(false);
            }

            @Override public void onReceivedError(
                    WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showStoryFailure("사진 이야기에 연결하지 못했어요.");
            }

            @Override public void onReceivedHttpError(
                    WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame() && response.getStatusCode() >= 400) {
                    showStoryFailure("사진 이야기를 불러오지 못했어요.");
                }
            }
        });
        contentHost.removeAllViews();
        contentHost.addView(web, new FrameLayout.LayoutParams(-1, -1));
        web.postUrl(bootstrap.url, bootstrap.postBody());
    }

    private void showStoryFailure(String message) {
        runOnUiThread(() -> {
            LinearLayout page = page("사진 이야기", message);
            TextView help = bodyText("Tailscale과 Mac 연결을 확인한 뒤 다시 시도해 주세요.");
            Button retry = primaryButton("다시 시도");
            retry.setOnClickListener(v -> showStory());
            LinearLayout content = new LinearLayout(this);
            content.setOrientation(LinearLayout.VERTICAL);
            content.addView(help);
            content.addView(space(dp(12)));
            content.addView(retry, matchWrap());
            page.addView(card(content));
            setScrollable(page);
            setLoading(false);
        });
    }

    private boolean isAllowedStoryUri(Uri candidate, Uri root) {
        if (!"https".equals(candidate.getScheme())) return false;
        if (!root.getHost().equals(candidate.getHost())) return false;
        if (root.getPort() != candidate.getPort()) return false;
        String rootPath = root.getPath() == null ? "" : root.getPath();
        String candidatePath = candidate.getPath() == null ? "" : candidate.getPath();
        return candidatePath.equals(rootPath) || candidatePath.startsWith(rootPath + "/");
    }

    private void showSettings() {
        selectSection("settings");
        LinearLayout page = page("설정", "GPS 원본 위치 동기화와 기기 연결을 관리합니다.");
        enrollmentInput = new EditText(this);
        enrollmentInput.setHint("ADB를 쓸 수 없을 때만 등록 JSON 붙여넣기");
        enrollmentInput.setMinLines(3);
        enrollmentInput.setMaxLines(7);
        enrollmentInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        enrollmentInput.setPadding(dp(14), dp(12), dp(14), dp(12));
        Button enrollButton = primaryButton("이 휴대폰 등록");
        enrollButton.setOnClickListener(v -> enroll());
        enrollmentSection = new LinearLayout(this);
        enrollmentSection.setOrientation(LinearLayout.VERTICAL);
        enrollmentSection.addView(enrollmentInput, matchWrap());
        enrollmentSection.addView(space(dp(8)));
        enrollmentSection.addView(enrollButton, matchWrap());

        syncButton = primaryButton("최근 사진 위치 지금 동기화");
        syncButton.setOnClickListener(v -> syncNow());
        Button people = quietButton("인물 확인 현황");
        people.setContentDescription("인물 확인 현황 열기, 읽기 전용");
        people.setOnClickListener(v -> showPeople());
        Button appSettings = quietButton("Android 권한 설정 열기");
        appSettings.setOnClickListener(v -> startActivity(new Intent(
                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + getPackageName()))));
        settingsStatus = bodyText("");

        LinearLayout controls = new LinearLayout(this);
        controls.setOrientation(LinearLayout.VERTICAL);
        controls.addView(enrollmentSection);
        controls.addView(syncButton);
        controls.addView(space(dp(8)));
        controls.addView(people);
        controls.addView(space(dp(8)));
        controls.addView(appSettings);
        controls.addView(settingsStatus);
        page.addView(card(controls));

        TextView versionStatus = bodyText(installedVersionLabel()
                + "\n서버 최신 버전을 확인하고 있어요…");
        page.addView(space(dp(12)));
        page.addView(card(versionStatus));
        setScrollable(page);
        refreshSettingsStatus("대기 중");
        refreshVersionStatus(versionStatus);
    }

    private String installedVersionLabel() {
        try {
            String version = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
            return "설치된 앱 버전 " + (version == null ? "확인 불가" : version);
        } catch (PackageManager.NameNotFoundException unavailable) {
            return "설치된 앱 버전 확인 불가";
        }
    }

    private void refreshVersionStatus(TextView versionStatus) {
        OwnerApiClient client = new OwnerApiClient(this);
        if (!client.canConnect()) {
            versionStatus.setText(installedVersionLabel()
                    + "\n기기를 등록하면 최신 버전을 확인할 수 있어요.");
            return;
        }
        executor.execute(() -> {
            try {
                JSONObject data = client.getCapabilities().getJSONObject("data");
                String latest = data.optString("latest_android_app_version", "").trim();
                String installed = getPackageManager()
                        .getPackageInfo(getPackageName(), 0).versionName;
                String result;
                if (latest.isEmpty()) {
                    result = installedVersionLabel() + "\n서버 최신 버전 정보가 없습니다.";
                } else if (latest.equals(installed)) {
                    result = installedVersionLabel() + "\n최신 버전입니다.";
                } else {
                    result = installedVersionLabel() + "\n업데이트 필요 · 최신 버전 " + latest;
                }
                String finalResult = result;
                runOnUiThread(() -> versionStatus.setText(finalResult));
            } catch (Exception error) {
                runOnUiThread(() -> versionStatus.setText(installedVersionLabel()
                        + "\n최신 버전 확인 실패 · Tailscale 연결을 확인해 주세요."));
            }
        });
    }

    private void showPeople() {
        selectSection("people");
        int generation = ++resultsGeneration;
        LinearLayout page = page(
                "인물 확인 현황",
                "확정된 이름과 사진 연결 상태를 확인합니다. 이름은 동의한 Story에만 표시됩니다.");
        Button backToSettings = quietButton("설정으로 돌아가기");
        backToSettings.setOnClickListener(v -> showSettings());
        page.addView(backToSettings, matchWrap());
        page.addView(space(dp(12)));

        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        TextView loading = bodyText("인물 현황을 불러오는 중…");
        content.addView(card(loading));
        page.addView(content);
        setScrollable(page);

        if (!new OwnerApiClient(this).canConnect()) {
            loading.setText("설정에서 휴대폰을 먼저 등록해 주세요.");
            return;
        }
        setLoading(true);
        executor.execute(() -> {
            try {
                OwnerApiClient client = new OwnerApiClient(this);
                JSONObject readiness = client.getPeopleReadiness().getJSONObject("data");
                JSONObject summary = client.getPeopleReviewSummary().getJSONObject("data");
                JSONArray people = client.getPeople().getJSONArray("data");
                JSONArray aliases = client.getPeopleAliases().getJSONArray("data");
                runOnUiThread(() -> {
                    if (generation != resultsGeneration || !"people".equals(currentSection)) {
                        return;
                    }
                    content.removeAllViews();

                    LinearLayout readinessCard = new LinearLayout(this);
                    readinessCard.setOrientation(LinearLayout.VERTICAL);
                    TextView readinessTitle = text("Story 인물 연결", 17, ink);
                    readinessTitle.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
                    if (Build.VERSION.SDK_INT >= 28) readinessTitle.setAccessibilityHeading(true);
                    readinessCard.addView(readinessTitle);
                    readinessCard.addView(bodyText(
                            readiness.optString("message", "인물 연결 상태를 확인했습니다.") + "\n"
                                    + "확정 이름 " + readiness.optInt("confirmed_identity_count") + " · "
                                    + "사진 연결 "
                                    + (readiness.optInt("confirmed_face_membership_count")
                                    + readiness.optInt("confirmed_asset_association_count")) + " · "
                                    + "이름 후보 " + readiness.optInt("pending_alias_count")));
                    content.addView(card(readinessCard));

                    if (aliases.length() > 0) {
                        LinearLayout aliasesCard = new LinearLayout(this);
                        aliasesCard.setOrientation(LinearLayout.VERTICAL);
                        TextView aliasesTitle = text("사진에서 찾은 이름", 17, ink);
                        aliasesTitle.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
                        if (Build.VERSION.SDK_INT >= 28) aliasesTitle.setAccessibilityHeading(true);
                        aliasesCard.addView(aliasesTitle);
                        aliasesCard.addView(bodyText(
                                "Apple Photos가 제공한 이름 후보입니다. 사진과 맞는 확정 인물을 선택해 주세요."));
                        for (int aliasIndex = 0; aliasIndex < aliases.length(); aliasIndex++) {
                            JSONObject alias = aliases.optJSONObject(aliasIndex);
                            if (alias == null) continue;
                            String aliasLabel = alias.optString("display_label", "이름 후보");
                            String aliasHandle = alias.optString("alias_action_handle", "");
                            TextView aliasName = text(aliasLabel, 16, ink);
                            aliasName.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
                            aliasName.setPadding(0, dp(12), 0, dp(4));
                            aliasesCard.addView(aliasName);
                            for (int personIndex = 0; personIndex < people.length(); personIndex++) {
                                JSONObject person = people.optJSONObject(personIndex);
                                if (person == null
                                        || !"user_confirmed".equals(person.optString("identity_status"))) {
                                    continue;
                                }
                                String personName = person.optString("display_name", "").trim();
                                String identityHandle = person.optString("consent_action_handle", "");
                                if (personName.isEmpty() || identityHandle.isEmpty()) continue;
                                Button connect = quietButton(personName + " 사진으로 연결");
                                connect.setContentDescription(
                                        aliasLabel + " 후보를 " + personName + " 인물로 연결");
                                connect.setOnClickListener(v -> confirmPersonAlias(
                                        aliasHandle, identityHandle, connect));
                                aliasesCard.addView(connect, matchWrap());
                                aliasesCard.addView(space(dp(6)));
                            }
                        }
                        content.addView(card(aliasesCard));
                    }

                    LinearLayout review = new LinearLayout(this);
                    review.setOrientation(LinearLayout.VERTICAL);
                    TextView reviewTitle = text("확인이 필요한 항목", 17, ink);
                    reviewTitle.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
                    review.addView(reviewTitle);
                    int total = summary.optInt("total_review_count");
                    review.addView(bodyText(total + "건\n"
                            + "인물 후보 " + summary.optInt("candidate_identity_count") + " · "
                            + "충돌 " + summary.optInt("conflicted_identity_count") + "\n"
                            + "사진 연결 후보 " + summary.optInt("candidate_membership_count") + " · "
                            + "이전 데이터 확인 " + summary.optInt("pending_lineage_hold_count")));
                    content.addView(card(review));

                    TextView listTitle = text("인물 목록", 18, ink);
                    listTitle.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
                    listTitle.setPadding(0, dp(8), 0, dp(10));
                    if (Build.VERSION.SDK_INT >= 28) listTitle.setAccessibilityHeading(true);
                    content.addView(listTitle);
                    if (people.length() == 0) {
                        content.addView(card(bodyText("아직 확인된 인물 정보가 없습니다.")));
                    }
                    for (int index = 0; index < people.length(); index++) {
                        JSONObject person = people.optJSONObject(index);
                        if (person == null) continue;
                        String name = person.optString("display_name", "").trim();
                        String state = person.optString("identity_status", "candidate");
                        String label;
                        if (!name.isEmpty()) {
                            label = name;
                        } else if ("conflicted".equals(state)) {
                            label = "구분 확인 필요";
                        } else if ("user_confirmed".equals(state)) {
                            label = "이름 비공개";
                        } else {
                            label = "확인 전 인물";
                        }
                        LinearLayout row = new LinearLayout(this);
                        row.setOrientation(LinearLayout.VERTICAL);
                        TextView identity = bodyText(label + "\n" + peopleStatusLabel(state));
                        identity.setContentDescription(
                                "인물 " + (index + 1) + ", " + label + ", "
                                        + peopleStatusLabel(state));
                        row.addView(identity, matchWrap());
                        if ("user_confirmed".equals(state)) {
                            JSONObject consents = person.optJSONObject("story_name_consent");
                            String actionHandle = person.optString(
                                    "consent_action_handle", "");
                            TextView explanation = text(
                                    "개인 Story는 소유자 화면에 이름을 표시합니다.\n"
                                            + "30일 가족 공유는 이름 포함을 선택한 공유 링크에서만 "
                                            + "최대 30일 표시합니다.",
                                    13,
                                    muted);
                            explanation.setLineSpacing(0, 1.2f);
                            explanation.setPadding(0, dp(12), 0, dp(4));
                            row.addView(explanation, matchWrap());

                            Switch personal = consentSwitch("개인 Story에 이름 표시");
                            Switch family = consentSwitch("30일 가족 공유에 이름 표시");
                            personal.setChecked(
                                    consents != null
                                            && consents.optBoolean("personal_story", false));
                            family.setChecked(
                                    consents != null
                                            && consents.optBoolean("family_share", false));
                            boolean actionable = actionHandle.matches(
                                    "pah_[A-Za-z0-9_-]{24,80}");
                            personal.setEnabled(actionable);
                            family.setEnabled(actionable);
                            personal.setOnCheckedChangeListener((button, checked) -> {
                                personal.setEnabled(false);
                                family.setEnabled(false);
                                updatePersonConsent(actionHandle, "personal_story", checked);
                            });
                            family.setOnCheckedChangeListener((button, checked) -> {
                                personal.setEnabled(false);
                                family.setEnabled(false);
                                updatePersonConsent(actionHandle, "family_share", checked);
                            });
                            row.addView(personal, matchWrap());
                            row.addView(family, matchWrap());
                        }
                        content.addView(card(row));
                    }
                    setLoading(false);
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (generation != resultsGeneration || !"people".equals(currentSection)) {
                        return;
                    }
                    loading.setText(connectionHelp(error));
                    setLoading(false);
                });
            }
        });
    }

    @SuppressWarnings("deprecation")
    private Switch consentSwitch(String label) {
        Switch control = new Switch(this);
        control.setText(label);
        control.setTextColor(ink);
        control.setTextSize(15);
        control.setMinHeight(dp(48));
        control.setGravity(Gravity.CENTER_VERTICAL);
        control.setContentDescription(label);
        return control;
    }

    private void updatePersonConsent(
            String actionHandle, String audience, boolean allowed) {
        setLoading(true);
        executor.execute(() -> {
            try {
                new OwnerApiClient(this).setPersonStoryNameConsent(
                        actionHandle, audience, allowed);
                runOnUiThread(this::showPeople);
            } catch (Exception error) {
                runOnUiThread(() -> {
                    boolean stale = error instanceof OwnerApiClient.OwnerApiException
                            && ((((OwnerApiClient.OwnerApiException) error).status == 409)
                            || (((OwnerApiClient.OwnerApiException) error).status == 404));
                    showPeople();
                    showMessage(
                            stale ? "인물 정보가 변경되었습니다" : "동의를 저장하지 못했어요",
                            stale
                                    ? "최신 상태를 다시 불러왔습니다. 확인한 뒤 다시 선택해 주세요."
                                    : connectionHelp(error));
                });
            }
        });
    }

    private void confirmPersonAlias(
            String aliasActionHandle, String identityActionHandle, Button button) {
        button.setEnabled(false);
        setLoading(true);
        executor.execute(() -> {
            try {
                new OwnerApiClient(this).confirmPersonAlias(
                        aliasActionHandle, identityActionHandle);
                runOnUiThread(() -> {
                    showMessage("사진 연결 완료", "Story의 인물 정보가 새로 반영되었습니다.");
                    showPeople();
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    button.setEnabled(true);
                    setLoading(false);
                    showMessage("사진을 연결하지 못했어요", connectionHelp(error));
                });
            }
        });
    }

    private void handleDebugEnrollment() {
        if ((getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) == 0) return;
        String encoded = getIntent().getStringExtra("debug_enrollment_b64");
        if (encoded == null) return;
        byte[] decoded = android.util.Base64.decode(encoded,
                android.util.Base64.URL_SAFE | android.util.Base64.NO_WRAP
                        | android.util.Base64.NO_PADDING);
        showSettings();
        enrollmentInput.setText(new String(decoded, java.nio.charset.StandardCharsets.UTF_8));
        if (getIntent().getBooleanExtra("debug_auto_enroll", false)) {
            enrollmentInput.post(this::enroll);
        }
    }

    private void enroll() {
        String value = enrollmentInput == null ? "" : enrollmentInput.getText().toString().trim();
        if (value.isEmpty()) {
            refreshSettingsStatus("등록 JSON이 필요합니다.");
            return;
        }
        setLoading(true);
        executor.execute(() -> {
            try {
                new ApiClient(this).enroll(value);
                SyncJobService.schedule(this);
                runOnUiThread(() -> {
                    enrollmentInput.setText("");
                    refreshSettingsStatus("등록 완료 · 일일 자동 동기화가 예약되었습니다.");
                    setLoading(false);
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    refreshSettingsStatus("등록 실패 · 새 등록 JSON으로 다시 시도해 주세요.");
                    setLoading(false);
                });
            }
        });
    }

    private void syncNow() {
        if (!hasMediaPermissions()) {
            requestMediaPermissions();
            refreshSettingsStatus("사진과 원본 위치 권한을 허용한 뒤 다시 눌러 주세요.");
            return;
        }
        syncButton.setEnabled(false);
        setLoading(true);
        refreshSettingsStatus("최근 사진의 위치를 확인하고 있습니다…");
        executor.execute(() -> {
            try {
                BridgeSync.Result result = BridgeSync.run(this);
                String message = getString(
                        R.string.sync_complete,
                        result.queued,
                        result.delivered,
                        result.remaining);
                runOnUiThread(() -> finishSync(message));
            } catch (SecurityException denied) {
                runOnUiThread(() -> finishSync("원본 위치 접근 권한이 필요합니다."));
            } catch (Exception error) {
                runOnUiThread(() -> finishSync(
                        "지금 전송하지 못했습니다. 암호화 큐를 보존하고 다음 실행에서 재시도합니다."));
            }
        });
    }

    private void finishSync(String message) {
        if (syncButton != null) syncButton.setEnabled(true);
        refreshSettingsStatus(message);
        setLoading(false);
    }

    private void refreshSettingsStatus(String prefix) {
        if (settingsStatus == null) return;
        boolean enrolled = new ApiClient(this).isEnrolled();
        if (enrollmentSection != null) enrollmentSection.setVisibility(enrolled ? View.GONE : View.VISIBLE);
        long last = getSharedPreferences("bridge", MODE_PRIVATE).getLong("last_sync_at", 0);
        String lastText = last == 0 ? "아직 없음"
                : DateFormat.getDateTimeInstance().format(new Date(last));
        settingsStatus.setText(getString(
                R.string.settings_status,
                prefix,
                enrolled ? "연결됨" : "등록 필요",
                lastText));
    }

    private void requestMediaPermissions() {
        List<String> needed = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 33) {
            if (checkSelfPermission(Manifest.permission.READ_MEDIA_IMAGES)
                    != PackageManager.PERMISSION_GRANTED) needed.add(Manifest.permission.READ_MEDIA_IMAGES);
        } else if (checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.READ_EXTERNAL_STORAGE);
        }
        if (Build.VERSION.SDK_INT >= 29
                && checkSelfPermission(Manifest.permission.ACCESS_MEDIA_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.ACCESS_MEDIA_LOCATION);
        }
        if (!needed.isEmpty()) requestPermissions(needed.toArray(new String[0]), PERMISSION_REQUEST);
    }

    private boolean hasMediaPermissions() {
        boolean read = Build.VERSION.SDK_INT >= 33
                ? checkSelfPermission(Manifest.permission.READ_MEDIA_IMAGES) == PackageManager.PERMISSION_GRANTED
                : checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED;
        boolean location = Build.VERSION.SDK_INT < 29
                || checkSelfPermission(Manifest.permission.ACCESS_MEDIA_LOCATION) == PackageManager.PERMISSION_GRANTED;
        return read && location;
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(requestCode, permissions, grants);
        if (requestCode == PERMISSION_REQUEST) {
            refreshSettingsStatus(hasMediaPermissions()
                    ? "권한 확인 완료 · 동기화 버튼을 다시 눌러 주세요."
                    : "사진·원본 위치 권한이 필요합니다.");
        }
    }

    private void handleBack() {
        if (recommendationViewer != null && recommendationViewer.isShowing()) {
            recommendationViewer.dismiss();
            return;
        }
        if ("story".equals(currentSection) && storyWebView != null) {
            WebView web = storyWebView;
            web.evaluateJavascript(
                    "(()=>{const d=document.querySelector('[data-viewer]');"
                            + "if(d&&d.open){d.close();return true}return false})()",
                    closed -> {
                        if ("true".equals(closed)) return;
                        if (web.canGoBack()) web.goBack(); else showHome();
                    });
            return;
        }
        if (!"home".equals(currentSection)) {
            showHome();
            return;
        }
        finishAfterTransition();
    }

    @SuppressLint("GestureBackNavigation")
    @Override public void onBackPressed() {
        handleBack();
    }

    @Override protected void onDestroy() {
        cancelManualPolling();
        if (recommendationViewer != null) recommendationViewer.dismiss();
        imageExecutor.shutdownNow();
        executor.shutdownNow();
        super.onDestroy();
    }

    @Override protected void onSaveInstanceState(Bundle state) {
        state.putString("manual_date_from", manualDateFrom.toString());
        state.putString("manual_date_to", manualDateTo.toString());
        state.putString("manual_selection_mode", manualSelectionMode);
        super.onSaveInstanceState(state);
    }

    private LinearLayout page(String title, String subtitle) {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(20), dp(24), dp(20), dp(32));
        TextView heading = text(title, 28, ink);
        heading.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        if (Build.VERSION.SDK_INT >= 28) heading.setAccessibilityHeading(true);
        TextView copy = text(subtitle, 15, muted);
        copy.setLineSpacing(0, 1.2f);
        copy.setPadding(0, dp(6), 0, dp(24));
        page.addView(heading);
        page.addView(copy);
        return page;
    }

    private View card(View child) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(16), dp(16), dp(16));
        card.setBackground(roundedBackground(this.card, dp(16), dp(1), outline));
        card.setElevation(dp(1));
        LinearLayout.LayoutParams params = matchWrap();
        params.setMargins(0, 0, 0, dp(12));
        card.setLayoutParams(params);
        card.addView(child, matchWrap());
        return card;
    }

    private View interactiveCard(View child, Runnable action) {
        View view = card(child);
        view.setClickable(true);
        view.setFocusable(true);
        view.setBackground(rippleBackground(card, dp(16)));
        view.setOnClickListener(ignored -> action.run());
        return view;
    }

    private void setScrollable(View page) {
        storyWebView = null;
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.addView(page);
        contentHost.removeAllViews();
        contentHost.addView(scroll, new FrameLayout.LayoutParams(-1, -1));
    }

    private TextView bodyText(String value) {
        TextView view = text(value, 16, ink);
        view.setLineSpacing(0, 1.3f);
        view.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        return view;
    }

    private Button primaryButton(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextColor(onAccent);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setBackground(rippleBackground(accent, dp(14)));
        button.setMinHeight(dp(48));
        return button;
    }

    private Button quietButton(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextColor(accent);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setBackground(rippleBackground(softAccent, dp(14)));
        button.setMinHeight(dp(48));
        return button;
    }

    private Button dangerButton(String label) {
        Button button = quietButton(label);
        button.setTextColor(getColor(R.color.photos_error));
        button.setContentDescription(label);
        return button;
    }

    private TextView text(String value, int sp, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(color);
        return view;
    }

    private void loadDesignTokens() {
        paper = getColor(R.color.photos_background);
        surface = getColor(R.color.photos_surface);
        card = getColor(R.color.photos_surface_raised);
        ink = getColor(R.color.photos_text_primary);
        muted = getColor(R.color.photos_text_secondary);
        accent = getColor(R.color.photos_primary);
        onAccent = getColor(R.color.photos_on_primary);
        softAccent = getColor(R.color.photos_primary_container);
        outline = getColor(R.color.photos_outline);
    }

    private boolean isDarkMode() {
        int nightMode = getResources().getConfiguration().uiMode
                & android.content.res.Configuration.UI_MODE_NIGHT_MASK;
        return nightMode == android.content.res.Configuration.UI_MODE_NIGHT_YES;
    }

    private Drawable roundedBackground(int fill, int radius, int strokeWidth, int strokeColor) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(fill);
        shape.setCornerRadius(radius);
        if (strokeWidth > 0) shape.setStroke(strokeWidth, strokeColor);
        return shape;
    }

    private Drawable rippleBackground(int fill, int radius) {
        Drawable content = roundedBackground(fill, radius, 0, Color.TRANSPARENT);
        Drawable mask = roundedBackground(Color.WHITE, radius, 0, Color.TRANSPARENT);
        int ripple = Color.argb(42, Color.red(accent), Color.green(accent), Color.blue(accent));
        return new RippleDrawable(ColorStateList.valueOf(ripple), content, mask);
    }

    private View space(int height) {
        View view = new View(this);
        view.setLayoutParams(new LinearLayout.LayoutParams(1, height));
        return view;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(-1, -2);
    }

    private String runSummary(JSONObject item) {
        String status = statusLabel(item.optString("status"));
        String created = formatKstTimestamp(item.optString("created_at"));
        String summary = status + (created.isEmpty() ? "" : "  ·  " + created)
                + "\n분석 " + item.optInt("processed_count") + "장"
                + "  ·  추천 " + item.optInt("recommended_count") + "장"
                + "  ·  저장 " + item.optInt("materialized_count") + "장"
                + (item.optInt("unfinished_count") > 0
                ? "\n남은 사진 " + item.optInt("unfinished_count") + "장 · 다음 실행에서 계속" : "");
        String failure = operationFailureDetail(item, item.optString("operation_id"), false);
        return failure.isEmpty() ? summary : summary + "\n\n" + failure;
    }

    private void renderOperationFailure(
            LinearLayout content, View container, String detail) {
        content.removeAllViews();
        if (detail.isEmpty()) {
            container.setVisibility(View.GONE);
            return;
        }
        TextView heading = text("오류 상세", 18, getColor(R.color.photos_error));
        heading.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        if (Build.VERSION.SDK_INT >= 28) heading.setAccessibilityHeading(true);
        TextView body = text(detail, 15, ink);
        body.setLineSpacing(0, 1.25f);
        body.setPadding(0, dp(8), 0, 0);
        body.setTextIsSelectable(true);
        content.addView(heading, matchWrap());
        content.addView(body, matchWrap());
        container.setContentDescription("오류 상세. " + detail);
        container.setVisibility(View.VISIBLE);
    }

    private String operationFailureDetail(
            JSONObject item, String operationId, boolean includeTechnicalIds) {
        String state = item.optString("status");
        String primaryCode = item.optString("error_code").trim();
        JSONArray failures = item.optJSONArray("source_errors");
        if (failures == null) failures = item.optJSONArray("children");
        if (!("failed".equals(state) || "interrupted".equals(state)
                || "cancelled".equals(state))
                && primaryCode.isEmpty() && (failures == null || failures.length() == 0)) {
            return "";
        }

        StringBuilder detail = new StringBuilder();
        if (!primaryCode.isEmpty()) {
            detail.append(errorMeaning(primaryCode));
        } else {
            detail.append("작업을 끝내지 못했습니다.");
        }
        String stage = item.optString("error_stage").trim();
        if (!stage.isEmpty()) {
            detail.append("\n발생 단계 · ").append(errorStageLabel(stage));
        }

        if (failures != null && failures.length() > 0) {
            detail.append("\n\n출처별 상태");
            for (int index = 0; index < failures.length(); index++) {
                JSONObject failure = failures.optJSONObject(index);
                if (failure == null) continue;
                String source = failure.optString("source",
                        failure.optString("provider"));
                String code = failure.optString("error_code").trim();
                String failureState = failure.optString("status").trim();
                detail.append("\n• ").append(providerLabel(source)).append(" · ")
                        .append(code.isEmpty() ? statusLabel(failureState) : errorMeaning(code));
            }
        }

        String resolution = errorResolution(primaryCode);
        if (!resolution.isEmpty()) {
            detail.append("\n\n해결 방법\n").append(resolution);
        }
        if (!primaryCode.isEmpty()) {
            detail.append("\n\n기술 정보\n오류 코드 · ").append(primaryCode);
        }
        if (includeTechnicalIds) {
            String runId = item.optString("run_id").trim();
            if (!operationId.isEmpty()) detail.append("\n작업 ID · ").append(operationId);
            if (!runId.isEmpty()) detail.append("\n실행 ID · ").append(runId);
        }
        return detail.toString();
    }

    private String providerLabel(String provider) {
        if ("google".equals(provider) || "google_photos".equals(provider)) {
            return "Google Photos";
        }
        if ("apple".equals(provider) || "apple_photos".equals(provider)) {
            return "Apple Photos";
        }
        return provider.isEmpty() ? "사진 출처" : provider;
    }

    private String errorStageLabel(String stage) {
        if ("google_mcp_readiness".equals(stage)) return "Google Photos 연결 확인";
        if ("provider_processing".equals(stage)) return "사진 출처 처리";
        if ("dispatch".equals(stage)) return "작업 시작";
        if ("workflow".equals(stage)) return "통합 사진 정리";
        return stage;
    }

    private String errorMeaning(String code) {
        if ("authentication_required".equals(code)) return "Google 계정 로그인이 필요합니다.";
        if ("consent_required".equals(code)) return "Google Photos 접근 동의가 필요합니다.";
        if ("captcha_required".equals(code)) return "Google 본인 확인을 완료해야 합니다.";
        if ("google_mcp_gate_failed".equals(code)) {
            return "Google Photos 확인이 끝나지 않아 분석을 시작하지 않았습니다.";
        }
        if ("chrome_mcp_unavailable".equals(code)) {
            return "PhotosMcp 전용 Chrome에 연결하지 못했습니다.";
        }
        if ("linux_model_unavailable".equals(code)) {
            return "사진 선택을 돕는 Linux 모델에 연결하지 못했습니다.";
        }
        if ("browser_mission_timeout".equals(code)) {
            return "Google Photos 선택 작업이 제한 시간 안에 끝나지 않았습니다.";
        }
        if ("picker_item_count_mismatch".equals(code)) {
            return "Google Photos 화면의 선택 수와 실제 전달된 사진 수가 일치하지 않습니다.";
        }
        if ("picker_worker_launch_failed".equals(code)) {
            return "Google Photos 선택 프로세스를 시작하지 못했습니다.";
        }
        if ("recommendation_root_unavailable".equals(code)
                || "recommendation_volume_unavailable".equals(code)) {
            return "추천 사진을 저장할 외장 보관소를 사용할 수 없습니다.";
        }
        if ("manual_dispatch_failed".equals(code) || "child_start_failed".equals(code)) {
            return "사진 분석 작업을 시작하지 못했습니다.";
        }
        if ("browser_mission_cancelled".equals(code) || "cancelled".equals(code)) {
            return "사진 선택 또는 분석 작업이 취소되었습니다.";
        }
        return "작업 처리 중 문제가 발생했습니다.";
    }

    private String errorResolution(String code) {
        if ("authentication_required".equals(code)) {
            return "Mac mini의 PhotosMcp 전용 Chrome에서 Google에 로그인한 뒤 새 작업을 실행해 주세요.";
        }
        if ("consent_required".equals(code)) {
            return "전용 Chrome에서 Google Photos Picker 접근을 허용한 뒤 다시 실행해 주세요.";
        }
        if ("captcha_required".equals(code)) {
            return "전용 Chrome에서 Google 본인 확인을 직접 완료한 뒤 다시 실행해 주세요.";
        }
        if ("chrome_mcp_unavailable".equals(code)
                || "picker_worker_launch_failed".equals(code)) {
            return "Mac mini에서 PhotosMcp와 전용 Chrome이 실행 중인지 확인한 뒤 다시 시도해 주세요.";
        }
        if ("linux_model_unavailable".equals(code)) {
            return "Linux 워크스테이션의 전원과 Smart Router 연결을 확인한 뒤 다시 실행해 주세요.";
        }
        if ("recommendation_root_unavailable".equals(code)
                || "recommendation_volume_unavailable".equals(code)) {
            return "Mac mini에 추천 보관용 외장 볼륨을 연결한 뒤 다시 실행해 주세요.";
        }
        if ("browser_mission_timeout".equals(code)) {
            return "Google Photos 화면을 확인하고 선택 범위나 최대 장수를 줄여 다시 실행해 주세요.";
        }
        if ("picker_item_count_mismatch".equals(code)) {
            return "불완전한 결과는 저장하지 않았습니다. Google Photos 선택을 새 작업으로 다시 실행해 주세요.";
        }
        if (code.isEmpty() || "google_mcp_gate_failed".equals(code)) return "";
        return "Mac mini의 PhotosMcp 상태와 작업 로그를 확인한 뒤 다시 실행해 주세요.";
    }

    private String resultSummary(JSONObject item) {
        String title = resultTitle(item);
        String date = item.optString("capture_date", "날짜 정보 없음");
        String location = item.optString("location", "위치 정보 없음");
        if (date.isBlank()) date = "날짜 정보 없음";
        if (location.isBlank()) location = "위치 미상";
        String locationStatus = item.optString("location_status", "unknown");
        String badge = "confirmed_gps".equals(locationStatus) ? "GPS 확인"
                : "contextual_estimate".equals(locationStatus) ? "문맥 추정" : "위치 미상";
        String locationDetail = location.equals(badge) ? location : location + "  ·  " + badge;
        String peopleCaption = item.optString("people_caption", "").trim();
        return title + "\n" + date + "  ·  " + locationDetail
                + (peopleCaption.isEmpty() ? "" : "\n" + peopleCaption);
    }

    private String resultTitle(JSONObject item) {
        String title = item.optString("title", "추천 사진");
        if (title.isBlank()) {
            int slot = item.optInt("recommendation_slot");
            title = slot > 0 ? "추천 사진 " + slot : "추천 사진";
        }
        return title;
    }

    private String resultCompactDetail(JSONObject item) {
        String peopleCaption = item.optString("people_caption", "").trim();
        if (!peopleCaption.isEmpty()) return peopleCaption;
        String date = item.optString("capture_date", "");
        String location = item.optString("location", "");
        if (date.isBlank()) date = "날짜 미상";
        if (location.isBlank()) location = "위치 미상";
        return date + " · " + location;
    }

    private String storyPeopleCaption(JSONObject story) {
        JSONArray overview = story.optJSONArray("people_overview");
        if (overview == null || overview.length() == 0) return "";
        List<String> names = new ArrayList<>();
        for (int index = 0; index < overview.length() && names.size() < 3; index++) {
            JSONObject person = overview.optJSONObject(index);
            if (person == null) continue;
            String name = person.optString("display_name", "").trim();
            if (!name.isEmpty() && !names.contains(name)) names.add(name);
        }
        if (names.isEmpty()) return "";
        String suffix = overview.length() > names.size() ? " 외" : "";
        return "함께한 사람: " + TextUtils.join(", ", names) + suffix;
    }

    private String eventSummary(JSONObject item) {
        String category = item.optString("category");
        String title = "action_required".equals(category) ? "사용자 확인 필요" : "사진 정리 결과";
        String stamp = formatKstTimestamp(item.optString("created_at"));
        return title + "  ·  " + statusLabel(item.optString("status"))
                + (stamp.isEmpty() ? "" : "\n" + stamp);
    }

    private String formatKstTimestamp(String value) {
        if (value == null || value.isBlank()) return "";
        try {
            return DISPLAY_TIMESTAMP.format(Instant.parse(value));
        } catch (RuntimeException ignored) {
            try {
                return DISPLAY_TIMESTAMP.format(java.time.OffsetDateTime.parse(value).toInstant());
            } catch (RuntimeException alsoIgnored) {
                return value.length() > 16
                        ? value.substring(0, 16).replace('T', ' ')
                        : value.replace('T', ' ');
            }
        }
    }

    private String statusLabel(String status) {
        if ("completed".equals(status)) return "완료";
        if ("completed_empty".equals(status)) return "완료 · 추천 없음";
        if ("partial".equals(status) || "partial_timeout".equals(status)) return "일부 완료";
        if ("failed".equals(status)) return "오류";
        if ("queued".equals(status)) return "대기 중";
        if ("dispatching".equals(status)) return "시작 준비 중";
        if ("cancelled".equals(status)) return "취소됨";
        if ("awaiting_user_action".equals(status)) return "사용자 확인 필요";
        if ("running".equals(status)) return "진행 중";
        return status.isEmpty() ? "확인 불가" : status;
    }

    private String peopleStatusLabel(String status) {
        if ("user_confirmed".equals(status)) return "확인됨";
        if ("conflicted".equals(status)) return "구분 검토 필요";
        if ("candidate".equals(status)) return "후보 · 확인 필요";
        return "확인 상태를 알 수 없음";
    }

    private String connectionHelp(Exception error) {
        String message = error.getMessage();
        if (message == null || message.isEmpty()) message = "연결하지 못했습니다.";
        return message + "\n\n확인할 것\n1. 휴대폰에서 Tailscale 켜기"
                + "\n2. Mac과 PhotosMcp 서비스가 켜져 있는지 확인"
                + "\n3. 같은 Tailnet 계정인지 확인"
                + "\n\nGPS 위치 동기화는 Tailscale 없이도 별도로 재시도됩니다.";
    }

    private void setLoading(boolean loading) {
        progress.setVisibility(loading ? View.VISIBLE : View.GONE);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
