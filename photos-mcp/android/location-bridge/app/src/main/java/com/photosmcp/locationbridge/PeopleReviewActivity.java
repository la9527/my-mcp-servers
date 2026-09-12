package com.photosmcp.locationbridge;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.ImageButton;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Face-level owner review. One photo may be linked to every person visible in it. */
public final class PeopleReviewActivity extends Activity {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final ExecutorService imageExecutor = Executors.newFixedThreadPool(4);
    private JSONArray photos = new JSONArray();
    private JSONObject drafts = new JSONObject();
    private JSONObject selectedAliases = new JSONObject();
    private int photoIndex = 0;
    private int faceIndex = 0;
    private int renderGeneration = 0;
    private boolean showContextPhoto = false;

    private int paper;
    private int surface;
    private int ink;
    private int muted;
    private int accent;
    private int onAccent;
    private int softAccent;
    private int outline;

    private LinearLayout content;
    private ProgressBar progress;
    private TextView heading;
    private TextView progressLabel;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        loadColors();
        getWindow().setStatusBarColor(paper);
        getWindow().setNavigationBarColor(paper);
        setContentView(buildShell());
        if (state != null) {
            try {
                photos = new JSONArray(state.getString("people_photos", "[]"));
                drafts = new JSONObject(state.getString("people_drafts", "{}"));
                selectedAliases = new JSONObject(state.getString("people_aliases", "{}"));
                photoIndex = Math.max(0, state.getInt("people_photo_index", 0));
                faceIndex = Math.max(0, state.getInt("people_face_index", 0));
                showContextPhoto = state.getBoolean("people_show_context", false);
            } catch (Exception ignored) {
                photos = new JSONArray();
                drafts = new JSONObject();
                selectedAliases = new JSONObject();
            }
        }
        if (photos.length() > 0) renderPhoto(); else loadReviews();
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putString("people_photos", photos.toString());
        out.putString("people_drafts", drafts.toString());
        out.putString("people_aliases", selectedAliases.toString());
        out.putInt("people_photo_index", photoIndex);
        out.putInt("people_face_index", faceIndex);
        out.putBoolean("people_show_context", showContextPhoto);
    }

    @Override protected void onDestroy() {
        renderGeneration++;
        executor.shutdownNow();
        imageExecutor.shutdownNow();
        super.onDestroy();
    }

    private View buildShell() {
        LinearLayout shell = new LinearLayout(this);
        shell.setOrientation(LinearLayout.VERTICAL);
        shell.setBackgroundColor(paper);

        LinearLayout bar = new LinearLayout(this);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(8), dp(6), dp(16), dp(6));
        bar.setMinimumHeight(dp(64));
        bar.setBackgroundColor(surface);
        ImageButton back = new ImageButton(this);
        back.setImageResource(R.drawable.ic_chevron_left);
        back.setBackgroundColor(android.graphics.Color.TRANSPARENT);
        back.setColorFilter(ink);
        back.setContentDescription("인물 관리로 돌아가기");
        back.setOnClickListener(v -> finish());
        bar.addView(back, new LinearLayout.LayoutParams(dp(48), dp(48)));
        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        heading = label("빠르게 인물 확인", 20, ink, true);
        progressLabel = label("사진을 불러오는 중", 13, muted, false);
        titles.addView(heading);
        titles.addView(progressLabel);
        bar.addView(titles, new LinearLayout.LayoutParams(0, -2, 1f));
        progress = new ProgressBar(this);
        progress.setIndeterminate(true);
        bar.addView(progress, new LinearLayout.LayoutParams(dp(32), dp(32)));
        shell.addView(bar, new LinearLayout.LayoutParams(-1, -2));

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(16), dp(16), dp(16), dp(32));
        scroll.addView(content, new ScrollView.LayoutParams(-1, -2));
        shell.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1f));
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

    private void loadReviews() {
        progress.setVisibility(View.VISIBLE);
        progressLabel.setText("확인할 예외를 불러오는 중");
        content.removeAllViews();
        content.addView(card(body("애매한 얼굴과 새로 자주 보이는 사람만 보여드립니다.")));
        executor.execute(() -> {
            try {
                JSONArray values = new OwnerApiClient(this).getFaceReviewPhotos()
                        .getJSONArray("data");
                runOnUiThread(() -> {
                    photos = values;
                    photoIndex = 0;
                    faceIndex = 0;
                    drafts = new JSONObject();
                    selectedAliases = new JSONObject();
                    progress.setVisibility(View.GONE);
                    renderPhoto();
                });
            } catch (Exception error) {
                runOnUiThread(() -> showFailure(error));
            }
        });
    }

    private void renderPhoto() {
        int generation = ++renderGeneration;
        content.removeAllViews();
        if (photos.length() == 0) {
            progress.setVisibility(View.GONE);
            progressLabel.setText("확인 대기 0장");
            TextView complete = label("모든 얼굴 확인을 마쳤어요", 22, ink, true);
            complete.setPadding(dp(4), dp(28), dp(4), dp(8));
            content.addView(complete);
            content.addView(body("새 인물 분석이 끝나면 이곳에 얼굴별 검토 항목이 나타납니다."));
            Button reload = primary("새로 고침");
            reload.setOnClickListener(v -> loadReviews());
            content.addView(space(20));
            content.addView(reload, fullButton());
            return;
        }
        photoIndex = Math.min(photoIndex, photos.length() - 1);
        JSONObject photo = photos.optJSONObject(photoIndex);
        if (photo == null) {
            loadReviews();
            return;
        }
        JSONArray faces = photo.optJSONArray("faces");
        if (faces == null) faces = new JSONArray();
        JSONArray resolvedAliases = photo.optJSONArray("resolved_alias_assignments");
        if (resolvedAliases != null && resolvedAliases.length() > 0 && faces.length() == 0) {
            progress.setVisibility(View.GONE);
            String date = photo.optString("capture_date_local", "");
            progressLabel.setText("사진 " + (photoIndex + 1) + " / " + photos.length()
                    + (date.isEmpty() ? "" : " · " + date));
            renderResolvedAliasCompletion(photo, resolvedAliases, generation);
            return;
        }
        faceIndex = faces.length() == 0 ? 0 : Math.min(faceIndex, faces.length() - 1);
        progress.setVisibility(View.GONE);
        String date = photo.optString("capture_date_local", "");
        progressLabel.setText("사진 " + (photoIndex + 1) + " / " + photos.length()
                + (date.isEmpty() ? "" : " · " + date));

        JSONObject selectedFace = faces.optJSONObject(faceIndex);
        String reviewKind = selectedFace == null ? "" : selectedFace.optString("review_kind", "");
        if ("promoted_new_person".equals(reviewKind)) {
            heading.setText("새로 자주 보이는 사람");
        } else if ("ambiguous_identity_match".equals(reviewKind)) {
            heading.setText("두 후보 비교");
        } else {
            heading.setText("빠르게 인물 확인");
        }

        JSONArray clusterEvidence = selectedFace == null
                ? null : selectedFace.optJSONArray("cluster_evidence_urls");
        if ("promoted_new_person".equals(reviewKind)
                && clusterEvidence != null && clusterEvidence.length() > 1) {
            TextView evidenceTitle = label(
                    "서로 다른 사진에서 확인된 얼굴 " + clusterEvidence.length() + "장",
                    15, ink, true);
            evidenceTitle.setPadding(0, dp(8), 0, dp(8));
            content.addView(evidenceTitle);
            HorizontalScrollView evidenceScroll = new HorizontalScrollView(this);
            evidenceScroll.setHorizontalScrollBarEnabled(false);
            LinearLayout evidenceRow = new LinearLayout(this);
            evidenceRow.setOrientation(LinearLayout.HORIZONTAL);
            for (int index = 0; index < clusterEvidence.length(); index++) {
                ImageView evidence = new ImageView(this);
                evidence.setScaleType(ImageView.ScaleType.CENTER_CROP);
                evidence.setImageResource(R.drawable.ic_image_placeholder);
                evidence.setColorFilter(muted);
                evidence.setContentDescription("반복 확인 얼굴 " + (index + 1));
                LinearLayout.LayoutParams evidenceParams = new LinearLayout.LayoutParams(
                        dp(108), dp(108));
                evidenceParams.setMargins(0, 0, dp(10), 0);
                evidenceRow.addView(evidence, evidenceParams);
                loadImage(evidence, clusterEvidence.optString(index, ""), generation);
            }
            evidenceScroll.addView(evidenceRow, new HorizontalScrollView.LayoutParams(-2, -2));
            content.addView(card(evidenceScroll));
        }

        LinearLayout faceHero = new LinearLayout(this);
        faceHero.setGravity(Gravity.CENTER);
        faceHero.setPadding(dp(12), dp(16), dp(12), dp(16));
        ImageView primaryFace = new ImageView(this);
        primaryFace.setScaleType(ImageView.ScaleType.CENTER_CROP);
        primaryFace.setImageResource(R.drawable.ic_image_placeholder);
        primaryFace.setColorFilter(muted);
        primaryFace.setContentDescription("확인할 얼굴 " + (faceIndex + 1));
        faceHero.addView(primaryFace, new LinearLayout.LayoutParams(dp(184), dp(184)));
        content.addView(card(faceHero));
        loadImage(primaryFace,
                selectedFace == null ? "" : selectedFace.optString("crop_image_url", ""),
                generation);

        Button contextToggle = secondary(showContextPhoto ? "사진 전체 숨기기" : "사진 전체 보기");
        contextToggle.setContentDescription("얼굴이 나온 원본 사진 "
                + (showContextPhoto ? "숨기기" : "보기"));
        contextToggle.setOnClickListener(v -> {
            showContextPhoto = !showContextPhoto;
            renderPhoto();
        });
        content.addView(space(8));
        content.addView(contextToggle, fullButton());
        if (showContextPhoto) {
            ImageView hero = new ImageView(this);
            hero.setScaleType(ImageView.ScaleType.FIT_CENTER);
            hero.setBackgroundColor(android.graphics.Color.rgb(14, 18, 16));
            hero.setImageResource(R.drawable.ic_image_placeholder);
            hero.setColorFilter(muted);
            hero.setContentDescription("전체 사진에서 선택한 얼굴 위치");
            LinearLayout.LayoutParams heroParams = new LinearLayout.LayoutParams(-1, dp(320));
            heroParams.setMargins(0, dp(8), 0, 0);
            content.addView(hero, heroParams);
            String heroUrl = selectedFace == null ? "" : selectedFace.optString("highlighted_image_url", "");
            if (heroUrl.isEmpty()) heroUrl = photo.optString("context_image_url", "");
            loadImage(hero, heroUrl, generation);
        }

        TextView instruction = body("확인할 얼굴만 골라 보여드립니다. 같은 사진에 확인할 사람이 여러 명이면 아래 얼굴을 바꿔 선택할 수 있어요.");
        instruction.setPadding(0, dp(12), 0, dp(8));
        content.addView(instruction);

        HorizontalScrollView stripScroll = new HorizontalScrollView(this);
        stripScroll.setHorizontalScrollBarEnabled(false);
        LinearLayout strip = new LinearLayout(this);
        strip.setOrientation(LinearLayout.HORIZONTAL);
        strip.setPadding(0, dp(4), 0, dp(8));
        for (int index = 0; index < faces.length(); index++) {
            JSONObject face = faces.optJSONObject(index);
            if (face == null) continue;
            int selectedIndex = index;
            LinearLayout faceCard = new LinearLayout(this);
            faceCard.setOrientation(LinearLayout.VERTICAL);
            faceCard.setGravity(Gravity.CENTER);
            faceCard.setPadding(dp(5), dp(5), dp(5), dp(7));
            faceCard.setMinimumWidth(dp(84));
            faceCard.setMinimumHeight(dp(112));
            faceCard.setBackground(roundRect(index == faceIndex ? softAccent : surface,
                    index == faceIndex ? accent : outline, 14));
            String faceHandle = face.optString("face_action_handle", "");
            String decision = draftLabel(faceHandle, face.optString("state", "pending"));
            faceCard.setContentDescription("얼굴 " + (index + 1) + ", " + decision
                    + (index == faceIndex ? ", 선택됨" : ""));
            faceCard.setOnClickListener(v -> {
                faceIndex = selectedIndex;
                renderPhoto();
            });
            ImageView crop = new ImageView(this);
            crop.setScaleType(ImageView.ScaleType.CENTER_CROP);
            crop.setImageResource(R.drawable.ic_image_placeholder);
            crop.setColorFilter(muted);
            faceCard.addView(crop, new LinearLayout.LayoutParams(dp(68), dp(68)));
            loadImage(crop, face.optString("crop_image_url", ""), generation);
            TextView cropLabel = label((index + 1) + " · " + decision, 12,
                    index == faceIndex ? accent : ink, index == faceIndex);
            cropLabel.setGravity(Gravity.CENTER);
            cropLabel.setMaxLines(2);
            faceCard.addView(cropLabel, new LinearLayout.LayoutParams(dp(76), -2));
            LinearLayout.LayoutParams faceParams = new LinearLayout.LayoutParams(dp(86), -2);
            faceParams.setMargins(0, 0, dp(8), 0);
            strip.addView(faceCard, faceParams);
        }
        stripScroll.addView(strip, new HorizontalScrollView.LayoutParams(-2, -2));
        content.addView(stripScroll, new LinearLayout.LayoutParams(-1, -2));

        JSONArray aliases = photo.optJSONArray("aliases");
        String faceHandle = selectedFace == null ? "" : selectedFace.optString("face_action_handle", "");
        if (aliases != null && aliases.length() > 0 && !faceHandle.isEmpty()) {
            TextView hint = label("Apple Photos 이름 힌트", 14, ink, true);
            hint.setPadding(0, dp(12), 0, dp(4));
            content.addView(hint);
            Button aliasChoice = secondary(selectedAliasLabel(faceHandle, aliases));
            aliasChoice.setContentDescription("선택한 얼굴에 연결할 Apple Photos 이름 힌트");
            aliasChoice.setOnClickListener(v -> chooseAlias(faceHandle, aliases));
            content.addView(aliasChoice, fullButton());
        }

        JSONObject suggestion = selectedFace == null
                ? null : selectedFace.optJSONObject("identity_suggestion");
        if (suggestion != null && !faceHandle.isEmpty()) {
            String suggestedName = suggestion.optString("display_name", "인물");
            int likelihood = suggestion.optInt("match_likelihood_percent", 0);
            int support = suggestion.optInt("supporting_photo_count", 0);
            boolean ready = "ready_to_confirm".equals(suggestion.optString("tier", ""));
            LinearLayout suggestionBody = new LinearLayout(this);
            suggestionBody.setOrientation(LinearLayout.VERTICAL);
            suggestionBody.addView(label("추천 인물 · " + suggestedName, 16, ink, true));
            suggestionBody.addView(space(5));
            suggestionBody.addView(body("일치 가능성 " + likelihood + "% · 확정 사진 "
                    + support + "장 기준\n" + suggestion.optString("explanation", "")));
            if (ready) {
                Button confirm = primary("맞아요 · " + suggestedName);
                confirm.setOnClickListener(v -> applySuggestion(selectedFace, suggestion, true));
                LinearLayout.LayoutParams confirmParams = fullButton();
                confirmParams.setMargins(0, dp(10), 0, 0);
                suggestionBody.addView(confirm, confirmParams);
            } else {
                Button apply = secondary("이 추천 적용");
                apply.setOnClickListener(v -> applySuggestion(selectedFace, suggestion, true));
                LinearLayout.LayoutParams applyParams = fullButton();
                applyParams.setMargins(0, dp(10), 0, 0);
                suggestionBody.addView(apply, applyParams);
            }
            content.addView(space(12));
            content.addView(card(suggestionBody));
        }

        TextView actionTitle = label("선택한 얼굴 " + (faceIndex + 1), 18, ink, true);
        actionTitle.setPadding(0, dp(20), 0, dp(8));
        content.addView(actionTitle);
        Button existing = primary("기존 인물에 연결");
        existing.setContentDescription("얼굴 " + (faceIndex + 1) + "을 기존 인물에 연결");
        existing.setOnClickListener(v -> chooseExistingPerson(photo, selectedFace));
        content.addView(existing, fullButton());
        content.addView(space(8));
        Button create = secondary("새 인물로 등록");
        create.setContentDescription("얼굴 " + (faceIndex + 1) + "에 새 이름 입력");
        create.setOnClickListener(v -> promptNewPerson(selectedFace));
        content.addView(create, fullButton());
        content.addView(space(8));
        Button ignoreUnknown = secondary("모르는 사람 · 무시");
        ignoreUnknown.setContentDescription(
                "실제 얼굴이지만 아는 사람이 아니므로 인물 목록과 Story에서 제외");
        ignoreUnknown.setOnClickListener(v -> setDecision(selectedFace, "ignore_unknown"));
        content.addView(ignoreUnknown, fullButton());

        LinearLayout alternatives = new LinearLayout(this);
        alternatives.setOrientation(LinearLayout.HORIZONTAL);
        alternatives.setPadding(0, dp(8), 0, 0);
        Button notFace = secondary("얼굴 아님");
        notFace.setOnClickListener(v -> setDecision(selectedFace, "not_a_face"));
        Button later = secondary("나중에");
        later.setOnClickListener(v -> setDecision(selectedFace, "defer"));
        alternatives.addView(notFace, weightedButton());
        alternatives.addView(spaceHorizontal(8));
        alternatives.addView(later, weightedButton());
        content.addView(alternatives, new LinearLayout.LayoutParams(-1, -2));

        LinearLayout saves = new LinearLayout(this);
        saves.setOrientation(LinearLayout.HORIZONTAL);
        saves.setPadding(0, dp(22), 0, 0);
        Button partial = secondary("중간 저장");
        partial.setOnClickListener(v -> savePhoto(false));
        Button next = primary("저장하고 다음");
        next.setOnClickListener(v -> savePhoto(true));
        saves.addView(partial, weightedButton());
        saves.addView(spaceHorizontal(8));
        saves.addView(next, weightedButton());
        content.addView(saves, new LinearLayout.LayoutParams(-1, -2));
    }

    private void renderResolvedAliasCompletion(
            JSONObject photo, JSONArray resolvedAliases, int generation) {
        heading.setText("이름 힌트 확인");
        LinearLayout summary = new LinearLayout(this);
        summary.setOrientation(LinearLayout.VERTICAL);
        summary.addView(label("얼굴 연결이 이미 완료됐어요", 20, ink, true));
        summary.addView(space(6));
        summary.addView(body(
                "확정된 얼굴과 Apple Photos 이름을 확인한 뒤 이 사진을 마무리하세요. "
                        + "기존 인물 연결은 변경되지 않습니다."));
        content.addView(card(summary));

        String contextUrl = photo.optString("context_image_url", "");
        if (!contextUrl.isEmpty()) {
            ImageView context = new ImageView(this);
            context.setScaleType(ImageView.ScaleType.FIT_CENTER);
            context.setBackgroundColor(android.graphics.Color.rgb(14, 18, 16));
            context.setImageResource(R.drawable.ic_image_placeholder);
            context.setColorFilter(muted);
            context.setContentDescription("확인 완료된 얼굴 위치가 표시된 사진");
            LinearLayout.LayoutParams contextParams = new LinearLayout.LayoutParams(-1, dp(280));
            contextParams.setMargins(0, dp(12), 0, dp(12));
            content.addView(context, contextParams);
            loadImage(context, contextUrl, generation);
        }

        for (int index = 0; index < resolvedAliases.length(); index++) {
            JSONObject item = resolvedAliases.optJSONObject(index);
            if (item == null) continue;
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(dp(12), dp(10), dp(12), dp(10));
            ImageView crop = new ImageView(this);
            crop.setScaleType(ImageView.ScaleType.CENTER_CROP);
            crop.setImageResource(R.drawable.ic_image_placeholder);
            crop.setColorFilter(muted);
            String confirmedName = item.optString("confirmed_display_name", "인물");
            crop.setContentDescription(confirmedName + " 확인 완료 얼굴");
            row.addView(crop, new LinearLayout.LayoutParams(dp(76), dp(76)));
            loadImage(crop, item.optString("crop_image_url", ""), generation);
            TextView relationship = label(
                    item.optString("alias_display_label", "이름 후보")
                            + "  →  " + confirmedName + "\n확인 완료",
                    15, ink, true);
            relationship.setPadding(dp(14), 0, 0, 0);
            row.addView(relationship, new LinearLayout.LayoutParams(0, -2, 1f));
            LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(-1, -2);
            rowParams.setMargins(0, 0, 0, dp(8));
            content.addView(card(row), rowParams);
        }

        Button complete = primary(
                "이 사진 이름 후보 " + resolvedAliases.length() + "건 완료");
        complete.setContentDescription(
                "확정된 얼굴은 그대로 두고 Apple Photos 이름 후보 "
                        + resolvedAliases.length() + "건 완료");
        complete.setEnabled(photo.optBoolean("can_complete_resolved_aliases", false));
        complete.setOnClickListener(v -> completeResolvedAliases(photo, resolvedAliases));
        content.addView(space(14));
        content.addView(complete, fullButton());

        Button reload = secondary("다시 불러오기");
        reload.setOnClickListener(v -> loadReviews());
        content.addView(space(8));
        content.addView(reload, fullButton());
    }

    private void completeResolvedAliases(JSONObject photo, JSONArray resolvedAliases) {
        JSONArray assignments = new JSONArray();
        for (int index = 0; index < resolvedAliases.length(); index++) {
            JSONObject resolution = resolvedAliases.optJSONObject(index);
            if (resolution == null) continue;
            try {
                JSONObject target = new JSONObject();
                target.put("kind", "existing");
                target.put("identity_action_handle",
                        resolution.optString("identity_action_handle", ""));
                JSONObject assignment = new JSONObject();
                assignment.put("face_action_handle",
                        resolution.optString("face_action_handle", ""));
                assignment.put("alias_action_handle",
                        resolution.optString("alias_action_handle", ""));
                assignment.put("target", target);
                assignments.put(assignment);
            } catch (Exception ignored) { }
        }
        if (assignments.length() != resolvedAliases.length()) {
            Toast.makeText(this, "이름 연결 정보를 다시 불러와 주세요.", Toast.LENGTH_LONG).show();
            return;
        }
        progress.setVisibility(View.VISIBLE);
        progressLabel.setText("이름 후보를 완료하는 중");
        String photoHandle = photo.optString("photo_review_handle", "");
        int revision = photo.optInt("review_revision", 1);
        executor.execute(() -> {
            try {
                new OwnerApiClient(this).applyFaceReview(
                        photoHandle, revision, assignments, new JSONArray());
                runOnUiThread(() -> {
                    Toast.makeText(this, "이 사진의 이름 확인을 마쳤습니다.",
                            Toast.LENGTH_SHORT).show();
                    loadReviews();
                });
            } catch (Exception error) {
                runOnUiThread(() -> showFailure(error));
            }
        });
    }

    private void chooseAlias(String faceHandle, JSONArray aliases) {
        String[] labels = new String[aliases.length() + 1];
        labels[0] = "연결하지 않음";
        for (int i = 0; i < aliases.length(); i++) {
            JSONObject alias = aliases.optJSONObject(i);
            labels[i + 1] = alias == null ? "이름 힌트" : alias.optString("display_label", "이름 힌트");
        }
        new AlertDialog.Builder(this)
                .setTitle("이 얼굴의 이름 힌트")
                .setItems(labels, (dialog, which) -> {
                    try {
                        if (which == 0) {
                            selectedAliases.remove(faceHandle);
                        } else {
                            JSONObject alias = aliases.optJSONObject(which - 1);
                            if (alias != null) {
                                String selectedHandle = alias.optString("alias_action_handle", "");
                                // One provider hint can identify only one face in this photo.
                                JSONArray names = selectedAliases.names();
                                if (names != null) {
                                    for (int index = 0; index < names.length(); index++) {
                                        String key = names.optString(index, "");
                                        if (selectedHandle.equals(selectedAliases.optString(key, ""))) {
                                            selectedAliases.remove(key);
                                        }
                                    }
                                }
                                selectedAliases.put(faceHandle, selectedHandle);
                            }
                        }
                    } catch (Exception ignored) { }
                    renderPhoto();
                })
                .show();
    }

    private void chooseExistingPerson(JSONObject photo, JSONObject face) {
        if (face == null) return;
        JSONArray choices = photo.optJSONArray("identity_choices");
        if (choices == null || choices.length() == 0) {
            Toast.makeText(this, "먼저 새 인물 이름을 등록해 주세요.", Toast.LENGTH_LONG).show();
            return;
        }
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        list.setPadding(dp(12), dp(8), dp(12), dp(8));
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("누구인가요?")
                .setMessage("이름과 대표 얼굴을 함께 확인한 뒤 선택하세요.")
                .setNegativeButton("취소", null)
                .create();
        for (int index = 0; index < choices.length(); index++) {
            JSONObject choice = choices.optJSONObject(index);
            if (choice == null) continue;
            LinearLayout choiceRow = new LinearLayout(this);
            choiceRow.setOrientation(LinearLayout.HORIZONTAL);
            choiceRow.setGravity(Gravity.CENTER_VERTICAL);
            choiceRow.setPadding(dp(10), dp(8), dp(10), dp(8));
            choiceRow.setMinimumHeight(dp(72));
            choiceRow.setBackground(roundRect(surface, outline, 14));
            ImageView portrait = new ImageView(this);
            portrait.setScaleType(ImageView.ScaleType.CENTER_CROP);
            portrait.setImageResource(R.drawable.ic_image_placeholder);
            portrait.setColorFilter(muted);
            String name = choice.optString("display_name", "인물");
            portrait.setContentDescription(name + " 대표 얼굴");
            choiceRow.addView(portrait, new LinearLayout.LayoutParams(dp(56), dp(56)));
            TextView choiceLabel = label(
                    name + "\n연결된 사진 " + choice.optInt("linked_photo_count", 0) + "장",
                    15, ink, true);
            choiceLabel.setPadding(dp(12), 0, 0, 0);
            choiceRow.addView(choiceLabel, new LinearLayout.LayoutParams(0, -2, 1f));
            loadImage(portrait, choice.optString("representative_face_url", ""), renderGeneration);
            choiceRow.setContentDescription(name + ", 연결된 사진 "
                    + choice.optInt("linked_photo_count", 0) + "장");
            choiceRow.setOnClickListener(v -> {
                String identityHandle = choice.optString("identity_action_handle", "");
                if (identityUsedByAnotherFace(identityHandle,
                        face.optString("face_action_handle", ""))) {
                    Toast.makeText(this, "이 사진의 다른 얼굴에 이미 선택한 인물입니다.",
                            Toast.LENGTH_LONG).show();
                    return;
                }
                JSONObject draft = new JSONObject();
                try {
                    draft.put("kind", "assignment");
                    draft.put("target_kind", "existing");
                    draft.put("identity_action_handle", identityHandle);
                    draft.put("display_name", name);
                    drafts.put(face.optString("face_action_handle", ""), draft);
                } catch (Exception ignored) { }
                dialog.dismiss();
                moveToNextPendingFace();
            });
            LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(-1, -2);
            rowParams.setMargins(0, 0, 0, dp(8));
            list.addView(choiceRow, rowParams);
        }
        ScrollView scroll = new ScrollView(this);
        scroll.addView(list, new ScrollView.LayoutParams(-1, -2));
        dialog.setView(scroll);
        dialog.show();
    }

    private void seedReadySuggestions(JSONArray faces) {
        for (int index = 0; index < faces.length(); index++) {
            JSONObject face = faces.optJSONObject(index);
            if (face == null) continue;
            String faceHandle = face.optString("face_action_handle", "");
            if (faceHandle.isEmpty() || drafts.has(faceHandle)) continue;
            JSONObject suggestion = face.optJSONObject("identity_suggestion");
            if (suggestion == null
                    || !"ready_to_confirm".equals(suggestion.optString("tier", ""))) continue;
            String identityHandle = suggestion.optString("identity_action_handle", "");
            if (identityHandle.isEmpty() || identityUsedByAnotherFace(identityHandle, faceHandle)) {
                continue;
            }
            boolean strongerDuplicateExists = false;
            for (int peerIndex = 0; peerIndex < faces.length(); peerIndex++) {
                if (peerIndex == index) continue;
                JSONObject peer = faces.optJSONObject(peerIndex);
                JSONObject peerSuggestion = peer == null
                        ? null : peer.optJSONObject("identity_suggestion");
                if (peerSuggestion != null
                        && identityHandle.equals(peerSuggestion.optString("identity_action_handle", ""))
                        && peerSuggestion.optInt("match_likelihood_percent", 0)
                        > suggestion.optInt("match_likelihood_percent", 0)) {
                    strongerDuplicateExists = true;
                    break;
                }
            }
            if (!strongerDuplicateExists) applySuggestion(face, suggestion, false);
        }
    }

    private void applySuggestion(JSONObject face, JSONObject suggestion, boolean rerender) {
        if (face == null || suggestion == null) return;
        String faceHandle = face.optString("face_action_handle", "");
        String identityHandle = suggestion.optString("identity_action_handle", "");
        if (faceHandle.isEmpty() || identityHandle.isEmpty()) return;
        if (identityUsedByAnotherFace(identityHandle, faceHandle)) {
            if (rerender) Toast.makeText(this,
                    "이 사진의 다른 얼굴에 같은 인물이 선택되어 있습니다.",
                    Toast.LENGTH_LONG).show();
            return;
        }
        JSONObject draft = new JSONObject();
        try {
            draft.put("kind", "assignment");
            draft.put("target_kind", "existing");
            draft.put("identity_action_handle", identityHandle);
            draft.put("display_name", suggestion.optString("display_name", "인물"));
            draft.put("suggestion_applied", true);
            drafts.put(faceHandle, draft);
        } catch (Exception ignored) { }
        if (rerender) renderPhoto();
    }

    private void promptNewPerson(JSONObject face) {
        if (face == null) return;
        EditText field = new EditText(this);
        field.setSingleLine(true);
        field.setHint("예: 엄마, 민서");
        field.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
        FrameLayout holder = new FrameLayout(this);
        holder.setPadding(dp(22), 0, dp(22), 0);
        holder.addView(field, new FrameLayout.LayoutParams(-1, -2));
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("새 인물 이름")
                .setMessage("선택한 얼굴에 사용할 이름 또는 가족 호칭을 입력하세요.")
                .setView(holder)
                .setNegativeButton("취소", null)
                .setPositiveButton("등록", null)
                .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(v -> {
                    String name = field.getText().toString().trim().replaceAll("\\s+", " ");
                    if (name.isEmpty() || name.length() > 80) {
                        field.setError("1~80자의 이름을 입력해 주세요.");
                        return;
                    }
                    JSONObject draft = new JSONObject();
                    try {
                        draft.put("kind", "assignment");
                        draft.put("target_kind", "new");
                        draft.put("display_name", name);
                        drafts.put(face.optString("face_action_handle", ""), draft);
                    } catch (Exception ignored2) { }
                    dialog.dismiss();
                    moveToNextPendingFace();
                }));
        dialog.show();
    }

    private void setDecision(JSONObject face, String decision) {
        if (face == null) return;
        JSONObject draft = new JSONObject();
        try {
            draft.put("kind", "decision");
            draft.put("decision", decision);
            String handle = face.optString("face_action_handle", "");
            drafts.put(handle, draft);
            selectedAliases.remove(handle);
        } catch (Exception ignored) { }
        moveToNextPendingFace();
    }

    private void moveToNextPendingFace() {
        JSONObject photo = photos.optJSONObject(photoIndex);
        JSONArray faces = photo == null ? null : photo.optJSONArray("faces");
        if (faces != null) {
            for (int offset = 1; offset <= faces.length(); offset++) {
                int candidate = (faceIndex + offset) % faces.length();
                JSONObject face = faces.optJSONObject(candidate);
                if (face != null && !drafts.has(face.optString("face_action_handle", ""))) {
                    faceIndex = candidate;
                    break;
                }
            }
        }
        renderPhoto();
    }

    private void savePhoto(boolean requireComplete) {
        JSONObject photo = photos.optJSONObject(photoIndex);
        JSONArray faces = photo == null ? null : photo.optJSONArray("faces");
        if (photo == null || faces == null) return;
        JSONArray assignments = new JSONArray();
        JSONArray decisions = new JSONArray();
        int missing = 0;
        for (int index = 0; index < faces.length(); index++) {
            JSONObject face = faces.optJSONObject(index);
            if (face == null) continue;
            String handle = face.optString("face_action_handle", "");
            JSONObject draft = drafts.optJSONObject(handle);
            if (draft == null) {
                if ("pending".equals(face.optString("state", "pending"))) missing++;
                continue;
            }
            try {
                if ("assignment".equals(draft.optString("kind"))) {
                    JSONObject item = new JSONObject();
                    item.put("face_action_handle", handle);
                    String aliasHandle = selectedAliases.optString(handle, "");
                    if (!aliasHandle.isEmpty()) item.put("alias_action_handle", aliasHandle);
                    JSONObject target = new JSONObject();
                    target.put("kind", draft.optString("target_kind"));
                    if ("existing".equals(draft.optString("target_kind"))) {
                        target.put("identity_action_handle",
                                draft.optString("identity_action_handle"));
                    } else {
                        target.put("display_name", draft.optString("display_name"));
                    }
                    item.put("target", target);
                    assignments.put(item);
                } else {
                    JSONObject item = new JSONObject();
                    item.put("face_action_handle", handle);
                    item.put("decision", draft.optString("decision"));
                    decisions.put(item);
                }
            } catch (Exception ignored) { }
        }
        if (assignments.length() + decisions.length() == 0) {
            Toast.makeText(this, "먼저 얼굴 하나 이상을 확인해 주세요.", Toast.LENGTH_LONG).show();
            return;
        }
        if (requireComplete && missing > 0) {
            new AlertDialog.Builder(this)
                    .setTitle("확인하지 않은 얼굴이 있어요")
                    .setMessage("남은 " + missing + "개 얼굴을 확인하고, 모르는 사람은 무시하거나 ‘나중에’로 지정해 주세요.")
                    .setPositiveButton("확인", null)
                    .show();
            return;
        }
        progress.setVisibility(View.VISIBLE);
        progressLabel.setText("얼굴별 인물 정보를 저장하는 중");
        String photoHandle = photo.optString("photo_review_handle", "");
        int revision = photo.optInt("review_revision", 1);
        executor.execute(() -> {
            try {
                new OwnerApiClient(this).applyFaceReview(
                        photoHandle, revision, assignments, decisions);
                runOnUiThread(() -> {
                    Toast.makeText(this, "얼굴별 인물 정보를 저장했습니다.", Toast.LENGTH_SHORT).show();
                    loadReviews();
                });
            } catch (Exception error) {
                runOnUiThread(() -> showFailure(error));
            }
        });
    }

    private boolean identityUsedByAnotherFace(String identityHandle, String currentFace) {
        JSONArray names = drafts.names();
        if (names == null) return false;
        for (int index = 0; index < names.length(); index++) {
            String face = names.optString(index, "");
            JSONObject draft = drafts.optJSONObject(face);
            if (!face.equals(currentFace) && draft != null
                    && identityHandle.equals(draft.optString("identity_action_handle", ""))) {
                return true;
            }
        }
        return false;
    }

    private String selectedAliasLabel(String faceHandle, JSONArray aliases) {
        String selected = selectedAliases.optString(faceHandle, "");
        for (int index = 0; index < aliases.length(); index++) {
            JSONObject alias = aliases.optJSONObject(index);
            if (alias != null && selected.equals(alias.optString("alias_action_handle", ""))) {
                return "이름 힌트: " + alias.optString("display_label", "이름 힌트");
            }
        }
        return "이름 힌트 연결 안 함";
    }

    private String draftLabel(String faceHandle, String serverState) {
        JSONObject draft = drafts.optJSONObject(faceHandle);
        if (draft == null) {
            if ("resolved".equals(serverState)) return "확정";
            if ("deferred".equals(serverState)) return "나중에";
            if ("ignored".equals(serverState)) return "무시됨";
            return "확인 전";
        }
        if ("assignment".equals(draft.optString("kind"))) {
            return draft.optString("display_name", "연결됨");
        }
        String decision = draft.optString("decision");
        if ("not_a_face".equals(decision)) return "얼굴 아님";
        if ("ignore_unknown".equals(decision)) return "모르는 사람 · 무시";
        return "나중에";
    }

    private void loadImage(ImageView image, String relativePath, int generation) {
        if (relativePath == null || relativePath.isEmpty()) return;
        imageExecutor.execute(() -> {
            try {
                byte[] encoded = new OwnerApiClient(this).getFaceReviewImage(relativePath);
                Bitmap bitmap = BitmapFactory.decodeByteArray(encoded, 0, encoded.length);
                if (bitmap == null) return;
                runOnUiThread(() -> {
                    if (generation != renderGeneration || isFinishing()) return;
                    image.clearColorFilter();
                    image.setImageBitmap(bitmap);
                });
            } catch (Exception ignored) { }
        });
    }

    private void showFailure(Exception error) {
        progress.setVisibility(View.GONE);
        progressLabel.setText("불러오지 못함");
        content.removeAllViews();
        content.addView(card(body("인물 검토 정보를 불러오거나 저장하지 못했습니다.\n\n"
                + failureMessage(error))));
        Button retry = primary("다시 시도");
        retry.setOnClickListener(v -> loadReviews());
        content.addView(space(16));
        content.addView(retry, fullButton());
    }

    private String failureMessage(Exception error) {
        if (error instanceof OwnerApiClient.OwnerApiException) {
            OwnerApiClient.OwnerApiException api = (OwnerApiClient.OwnerApiException) error;
            String code = api.code == null ? "" : api.code;
            if ("stale_review_revision".equals(code)) return "다른 화면에서 정보가 변경되었습니다. 다시 불러와 주세요.";
            if ("face_review_rejected".equals(code)) return "얼굴과 인물 선택이 충돌했습니다. 한 얼굴씩 다시 확인해 주세요.";
            return api.getMessage() + (code.isEmpty() ? "" : "\n오류 코드: " + code);
        }
        return error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
    }

    private void loadColors() {
        paper = getColor(R.color.photos_background);
        surface = getColor(R.color.photos_surface_raised);
        ink = getColor(R.color.photos_text_primary);
        muted = getColor(R.color.photos_text_secondary);
        accent = getColor(R.color.photos_primary);
        onAccent = getColor(R.color.photos_on_primary);
        softAccent = getColor(R.color.photos_primary_container);
        outline = getColor(R.color.photos_outline);
    }

    private TextView label(String value, int size, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setTypeface(Typeface.DEFAULT, bold ? Typeface.BOLD : Typeface.NORMAL);
        return view;
    }

    private TextView body(String value) {
        TextView view = label(value, 15, muted, false);
        view.setLineSpacing(0, 1.22f);
        return view;
    }

    private View card(View child) {
        FrameLayout frame = new FrameLayout(this);
        frame.setPadding(dp(16), dp(16), dp(16), dp(16));
        frame.setBackground(roundRect(surface, outline, 18));
        frame.addView(child, new FrameLayout.LayoutParams(-1, -2));
        return frame;
    }

    private Button primary(String value) {
        Button button = new Button(this);
        button.setText(value);
        button.setTextColor(onAccent);
        button.setTextSize(15);
        button.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        button.setAllCaps(false);
        button.setMinimumHeight(dp(52));
        button.setBackground(roundRect(accent, accent, 14));
        return button;
    }

    private Button secondary(String value) {
        Button button = new Button(this);
        button.setText(value);
        button.setTextColor(ink);
        button.setTextSize(15);
        button.setAllCaps(false);
        button.setMinimumHeight(dp(52));
        button.setBackground(roundRect(surface, outline, 14));
        return button;
    }

    private GradientDrawable roundRect(int fill, int stroke, int radius) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(fill);
        drawable.setCornerRadius(dp(radius));
        drawable.setStroke(dp(1), stroke);
        return drawable;
    }

    private LinearLayout.LayoutParams fullButton() {
        return new LinearLayout.LayoutParams(-1, dp(52));
    }

    private LinearLayout.LayoutParams weightedButton() {
        return new LinearLayout.LayoutParams(0, dp(52), 1f);
    }

    private View space(int value) {
        View view = new View(this);
        view.setLayoutParams(new LinearLayout.LayoutParams(1, dp(value)));
        return view;
    }

    private View spaceHorizontal(int value) {
        View view = new View(this);
        view.setLayoutParams(new LinearLayout.LayoutParams(dp(value), 1));
        return view;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
