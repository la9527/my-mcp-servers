package com.photosmcp.locationbridge;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/** Tailnet-only, device-bound client for privacy-safe PhotosMcp projections. */
final class OwnerApiClient {
    private static final String PREFS = "bridge";
    private static final String API = "/mobile-client/v1";
    private static volatile String processAccessToken = "";
    private static volatile long processTokenExpiresAt = 0L;
    private final Context context;

    OwnerApiClient(Context context) {
        this.context = context.getApplicationContext();
    }

    boolean canConnect() {
        SharedPreferences preferences = prefs();
        return !preferences.getString("server_base", "").isEmpty()
                && !preferences.getString("device_id", "").isEmpty()
                && !preferences.getString("key_id", "").isEmpty();
    }

    JSONObject getDashboard() throws Exception {
        return getJson(API + "/dashboard");
    }

    JSONObject getCapabilities() throws Exception {
        return getJson(API + "/capabilities");
    }

    JSONObject getPeople() throws Exception {
        return getJson(API + "/people");
    }

    JSONObject getPeopleReviewSummary() throws Exception {
        return getJson(API + "/people/review-summary");
    }

    JSONObject getPeopleReadiness() throws Exception {
        return getJson(API + "/people/readiness");
    }

    JSONObject getPeopleAliases() throws Exception {
        return getJson(API + "/people/aliases");
    }

    JSONObject confirmPersonAlias(
            String aliasActionHandle, String identityActionHandle) throws Exception {
        if (aliasActionHandle == null
                || !aliasActionHandle.matches("aal_[A-Za-z0-9_-]{24,80}")) {
            throw new IllegalArgumentException("invalid alias action handle");
        }
        if (identityActionHandle == null
                || !identityActionHandle.matches("pah_[A-Za-z0-9_-]{24,80}")) {
            throw new IllegalArgumentException("invalid identity action handle");
        }
        String path = API + "/people/alias/confirm";
        JSONObject payload = new JSONObject();
        payload.put("schema_version", 1);
        payload.put("alias_action_handle", aliasActionHandle);
        payload.put("identity_action_handle", identityActionHandle);
        String body = payload.toString();
        String bodyHash = BridgeKeys.hex(
                MessageDigest.getInstance("SHA-256").digest(
                        body.getBytes(StandardCharsets.UTF_8)));
        String idempotencyKey = "person-alias-" + UUID.randomUUID();
        String nonce = "nonce-" + UUID.randomUUID();
        String createdAt = Instant.now().toString();
        String message = "OWNER-COMMAND-V1\nPOST\n" + path + "\n" + bodyHash + "\n"
                + nonce + "\n" + idempotencyKey + "\n" + createdAt;
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("Idempotency-Key", idempotencyKey);
        headers.put("X-Command-Nonce", nonce);
        headers.put("X-Command-Created-At", createdAt);
        headers.put("X-Device-Signature", BridgeKeys.signOwner(
                message.getBytes(StandardCharsets.UTF_8)));
        return authorized("POST", path, body, true, headers);
    }

    JSONObject setPersonStoryNameConsent(
            String actionHandle, String audience, boolean allowed) throws Exception {
        if (actionHandle == null
                || !actionHandle.matches("pah_[A-Za-z0-9_-]{24,80}")) {
            throw new IllegalArgumentException("invalid consent action handle");
        }
        if (!("personal_story".equals(audience) || "family_share".equals(audience))) {
            throw new IllegalArgumentException("invalid consent audience");
        }
        String path = API + "/people/consent";
        JSONObject payload = new JSONObject();
        payload.put("schema_version", 1);
        payload.put("consent_action_handle", actionHandle);
        payload.put("audience", audience);
        payload.put("allowed", allowed);
        String body = payload.toString();
        String bodyHash = BridgeKeys.hex(
                MessageDigest.getInstance("SHA-256").digest(
                        body.getBytes(StandardCharsets.UTF_8)));
        String idempotencyKey = "person-consent-" + UUID.randomUUID();
        String nonce = "nonce-" + UUID.randomUUID();
        String createdAt = Instant.now().toString();
        String message = "OWNER-COMMAND-V1\nPOST\n" + path + "\n" + bodyHash + "\n"
                + nonce + "\n" + idempotencyKey + "\n" + createdAt;
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("Idempotency-Key", idempotencyKey);
        headers.put("X-Command-Nonce", nonce);
        headers.put("X-Command-Created-At", createdAt);
        headers.put("X-Device-Signature", BridgeKeys.signOwner(
                message.getBytes(StandardCharsets.UTF_8)));
        return authorized("POST", path, body, true, headers);
    }

    JSONObject getRuns() throws Exception {
        return getJson(API + "/runs?limit=20");
    }

    JSONObject getResults() throws Exception {
        return getJson(API + "/results?limit=48");
    }

    JSONObject getResults(String storyId) throws Exception {
        if (storyId == null || storyId.isEmpty()) return getResults();
        requireSafeId(storyId);
        return getJson(API + "/results?limit=100&story_id=" + storyId);
    }

    JSONObject getStories() throws Exception {
        return getJson(API + "/stories");
    }

    JSONObject reanalyzeStory(String storyId, JSONObject locationPrefetch) throws Exception {
        requireSafeId(storyId);
        return signedStoryCommand(storyId, "reanalyze", "reanalyze-", locationPrefetch);
    }

    JSONObject deleteStory(String storyId) throws Exception {
        requireSafeId(storyId);
        return signedStoryCommand(storyId, "delete", "story-delete-", null);
    }

    JSONObject previewManualCuration(JSONObject payload) throws Exception {
        return authorized("POST", API + "/manual-curations/preview", payload.toString(), true);
    }

    JSONObject startManualCuration(JSONObject payload) throws Exception {
        String path = API + "/manual-curations";
        String body = payload.toString();
        String bodyHash = BridgeKeys.hex(
                MessageDigest.getInstance("SHA-256").digest(body.getBytes(StandardCharsets.UTF_8)));
        SharedPreferences preferences = prefs();
        String idempotencyKey = preferences.getString("pending_manual_idempotency", "");
        if (!bodyHash.equals(preferences.getString("pending_manual_body_hash", ""))
                || !idempotencyKey.matches("[A-Za-z0-9._:-]{8,160}")) {
            idempotencyKey = "manual-" + UUID.randomUUID();
            preferences.edit()
                    .putString("pending_manual_body_hash", bodyHash)
                    .putString("pending_manual_idempotency", idempotencyKey)
                    .commit();
        }
        String nonce = "nonce-" + UUID.randomUUID();
        String createdAt = Instant.now().toString();
        String message = "OWNER-COMMAND-V1\nPOST\n" + path + "\n" + bodyHash + "\n"
                + nonce + "\n" + idempotencyKey + "\n" + createdAt;
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("Idempotency-Key", idempotencyKey);
        headers.put("X-Command-Nonce", nonce);
        headers.put("X-Command-Created-At", createdAt);
        headers.put("X-Device-Signature", BridgeKeys.signOwner(
                message.getBytes(StandardCharsets.UTF_8)));
        JSONObject receipt = authorized("POST", path, body, true, headers);
        preferences.edit()
                .remove("pending_manual_body_hash")
                .remove("pending_manual_idempotency")
                .commit();
        return receipt;
    }

    JSONObject getManualOperation(String operationId) throws Exception {
        requireSafeId(operationId);
        return getJson(API + "/manual-curations/" + operationId);
    }

    private JSONObject signedStoryCommand(
            String storyId, String action, String idempotencyPrefix,
            JSONObject locationPrefetch) throws Exception {
        String path = API + "/stories/" + storyId + "/" + action;
        JSONObject payload = new JSONObject();
        payload.put("schema_version", 1);
        if (locationPrefetch != null) payload.put("location_prefetch", locationPrefetch);
        String body = payload.toString();
        String bodyHash = BridgeKeys.hex(
                MessageDigest.getInstance("SHA-256").digest(body.getBytes(StandardCharsets.UTF_8)));
        String idempotencyKey = idempotencyPrefix + UUID.randomUUID();
        String nonce = "nonce-" + UUID.randomUUID();
        String createdAt = Instant.now().toString();
        String message = "OWNER-COMMAND-V1\nPOST\n" + path + "\n" + bodyHash + "\n"
                + nonce + "\n" + idempotencyKey + "\n" + createdAt;
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("Idempotency-Key", idempotencyKey);
        headers.put("X-Command-Nonce", nonce);
        headers.put("X-Command-Created-At", createdAt);
        headers.put("X-Device-Signature", BridgeKeys.signOwner(
                message.getBytes(StandardCharsets.UTF_8)));
        return authorized("POST", path, body, true, headers);
    }

    byte[] getResultImage(String assetId, String kind) throws Exception {
        return getResultImage(assetId, kind, "");
    }

    byte[] getResultImage(String assetId, String kind, String storyId) throws Exception {
        if (!assetId.matches("[A-Za-z0-9._:-]{8,160}")
                || !("thumb".equals(kind) || "preview".equals(kind))) {
            throw new IllegalArgumentException("invalid result image request");
        }
        String suffix = "";
        if (storyId != null && !storyId.isEmpty()) {
            requireSafeId(storyId);
            suffix = "?story_id=" + storyId;
        }
        BinaryHttpResult response = authorizedBinary(
                API + "/results/assets/" + assetId + "/" + kind + suffix, true);
        if (!response.contentType.startsWith("image/jpeg")) {
            throw new IllegalStateException("unexpected result image type");
        }
        return response.body;
    }

    JSONObject getEvents() throws Exception {
        return getJson(API + "/events");
    }

    void acknowledgeEvent(String eventId) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("event_id", eventId);
        authorized("POST", API + "/events/ack", payload.toString(), true);
    }

    WebBootstrap createWebBootstrap() throws Exception {
        return createWebBootstrap("");
    }

    WebBootstrap createWebBootstrap(String storyId) throws Exception {
        JSONObject payload = new JSONObject();
        if (storyId != null && !storyId.isEmpty()) {
            requireSafeId(storyId);
            payload.put("story_id", storyId);
        }
        JSONObject response = authorized(
                "POST", API + "/web-exchange", payload.toString(), true);
        JSONObject data = response.getJSONObject("data");
        return new WebBootstrap(
                ownerOrigin() + data.getString("bootstrap_url"),
                data.getString("exchange_code"));
    }

    String storyOrigin() throws Exception {
        return ownerOrigin() + "/mobile-client/story";
    }

    private JSONObject getJson(String path) throws Exception {
        return authorized("GET", path, null, true);
    }

    private JSONObject authorized(
            String method, String path, String body, boolean allowSessionRefresh) throws Exception {
        return authorized(method, path, body, allowSessionRefresh, Collections.emptyMap());
    }

    private JSONObject authorized(
            String method, String path, String body, boolean allowSessionRefresh,
            Map<String, String> extraHeaders) throws Exception {
        String token = accessToken();
        HttpResult response = request(method, ownerOrigin() + path, body, token, extraHeaders);
        if (response.status == 401 && allowSessionRefresh) {
            clearSession();
            response = request(method, ownerOrigin() + path, body, accessToken(), extraHeaders);
        }
        if (response.status < 200 || response.status >= 300) {
            throw new OwnerApiException(
                    response.status,
                    responseErrorCode(response.body),
                    friendlyFailure(response.status, responseErrorCode(response.body)));
        }
        if (response.status == 204 || response.body.isEmpty()) return new JSONObject();
        return new JSONObject(response.body);
    }

    private BinaryHttpResult authorizedBinary(String path, boolean allowSessionRefresh)
            throws Exception {
        BinaryHttpResult response = requestBinary(ownerOrigin() + path, accessToken());
        if (response.status == 401 && allowSessionRefresh) {
            clearSession();
            response = requestBinary(ownerOrigin() + path, accessToken());
        }
        if (response.status < 200 || response.status >= 300) {
            throw new OwnerApiException(response.status, "", friendlyFailure(response.status, ""));
        }
        if (response.body.length == 0 || response.body.length > 10 * 1024 * 1024) {
            throw new IllegalStateException("invalid result image size");
        }
        return response;
    }

    private synchronized String accessToken() throws Exception {
        if (!processAccessToken.isEmpty()
                && processTokenExpiresAt > System.currentTimeMillis() + 30_000L) {
            return processAccessToken;
        }
        ensureOwnerEnrollment();
        JSONObject challenge = challenge("owner_session");
        String deviceId = prefs().getString("device_id", "");
        String ownerKeyId = ownerKeyId();
        String message = "OWNER-SESSION-V1\n"
                + challenge.getString("challenge_id") + "\n"
                + challenge.getString("nonce") + "\n"
                + deviceId + "\n" + ownerKeyId;
        JSONObject payload = new JSONObject();
        payload.put("schema_version", 1);
        payload.put("device_id", deviceId);
        payload.put("owner_key_id", ownerKeyId);
        payload.put("challenge_id", challenge.getString("challenge_id"));
        payload.put("nonce", challenge.getString("nonce"));
        payload.put("signature", BridgeKeys.signOwner(message.getBytes(StandardCharsets.UTF_8)));
        HttpResult response = request(
                "POST", ownerOrigin() + API + "/session", payload.toString(), null);
        if (response.status != 201) {
            if (response.status == 401) {
                prefs().edit().remove("owner_enrolled").apply();
            }
            String code = responseErrorCode(response.body);
            throw new OwnerApiException(response.status, code, friendlyFailure(response.status, code));
        }
        JSONObject data = new JSONObject(response.body).getJSONObject("data");
        processAccessToken = data.getString("access_token");
        processTokenExpiresAt = Instant.parse(data.getString("expires_at")).toEpochMilli();
        return processAccessToken;
    }

    private void ensureOwnerEnrollment() throws Exception {
        if (prefs().getBoolean("owner_enrolled", false)) return;
        JSONObject challenge = challenge("owner_enroll");
        String deviceId = prefs().getString("device_id", "");
        String ingestKeyId = prefs().getString("key_id", "");
        String ownerKeyId = ownerKeyId();
        String ownerPem = BridgeKeys.ownerPublicKeyPem();
        String fingerprint = BridgeKeys.hex(
                MessageDigest.getInstance("SHA-256").digest(
                        ownerPem.getBytes(StandardCharsets.US_ASCII)));
        String message = "OWNER-ENROLL-V1\n"
                + challenge.getString("challenge_id") + "\n"
                + challenge.getString("nonce") + "\n"
                + deviceId + "\n" + ingestKeyId + "\n" + ownerKeyId + "\n" + fingerprint;
        JSONObject payload = new JSONObject();
        payload.put("schema_version", 1);
        payload.put("device_id", deviceId);
        payload.put("ingest_key_id", ingestKeyId);
        payload.put("purpose", "owner_enroll");
        payload.put("challenge_id", challenge.getString("challenge_id"));
        payload.put("nonce", challenge.getString("nonce"));
        payload.put("owner_key_id", ownerKeyId);
        payload.put("owner_public_key_pem", ownerPem);
        payload.put("signature", BridgeKeys.sign(message.getBytes(StandardCharsets.UTF_8)));
        HttpResult response = request(
                "POST", ownerOrigin() + API + "/owner-enroll", payload.toString(), null);
        if (response.status != 201) {
            String code = responseErrorCode(response.body);
            throw new OwnerApiException(response.status, code, friendlyFailure(response.status, code));
        }
        prefs().edit().putBoolean("owner_enrolled", true).commit();
    }

    private JSONObject challenge(String purpose) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("schema_version", 1);
        payload.put("device_id", prefs().getString("device_id", ""));
        payload.put("ingest_key_id", prefs().getString("key_id", ""));
        payload.put("purpose", purpose);
        HttpResult response = request(
                "POST", ownerOrigin() + API + "/challenge", payload.toString(), null);
        if (response.status != 201) {
            String code = responseErrorCode(response.body);
            throw new OwnerApiException(response.status, code, friendlyFailure(response.status, code));
        }
        return new JSONObject(response.body).getJSONObject("data");
    }

    private String ownerKeyId() throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256")
                .digest(BridgeKeys.ownerKey().getPublic().getEncoded());
        return "owner-key-" + BridgeKeys.hex(digest).substring(0, 32);
    }

    private String ownerOrigin() throws Exception {
        String configured = prefs().getString("owner_origin", "").trim();
        URL publicReceiver = new URL(prefs().getString("server_base", ""));
        if (!configured.isEmpty()) {
            URL parsed = new URL(configured);
            if (!"https".equals(parsed.getProtocol())
                    || parsed.getHost().isEmpty()
                    || (!parsed.getPath().isEmpty() && !"/".equals(parsed.getPath()))
                    || parsed.getUserInfo() != null
                    || parsed.getQuery() != null
                    || parsed.getRef() != null
                    || (parsed.getPort() != -1 && parsed.getPort() != 443)
                    || !parsed.getHost().equals(publicReceiver.getHost())) {
                throw new IllegalArgumentException("invalid owner origin");
            }
            return configured.endsWith("/")
                    ? configured.substring(0, configured.length() - 1) : configured;
        }
        if (!"https".equals(publicReceiver.getProtocol()) || publicReceiver.getHost().isEmpty()) {
            throw new IllegalArgumentException("invalid PhotosMcp host");
        }
        return "https://" + publicReceiver.getHost();
    }

    private HttpResult request(String method, String url, String body, String bearer) throws Exception {
        return request(method, url, body, bearer, Collections.emptyMap());
    }

    private HttpResult request(
            String method, String url, String body, String bearer,
            Map<String, String> extraHeaders) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(20_000);
        connection.setReadTimeout(35_000);
        connection.setRequestMethod(method);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Accept", "application/json");
        if (bearer != null && !bearer.isEmpty()) {
            connection.setRequestProperty("Authorization", "Bearer " + bearer);
        }
        for (Map.Entry<String, String> entry : extraHeaders.entrySet()) {
            connection.setRequestProperty(entry.getKey(), entry.getValue());
        }
        if (body != null) {
            byte[] encoded = body.getBytes(StandardCharsets.UTF_8);
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(encoded.length);
            connection.setRequestProperty("Content-Type", "application/json");
            try (OutputStream output = connection.getOutputStream()) {
                output.write(encoded);
            }
        }
        int status = connection.getResponseCode();
        InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
        String responseBody = stream == null ? "{}" : readSmall(stream);
        connection.disconnect();
        return new HttpResult(status, responseBody);
    }

    private BinaryHttpResult requestBinary(String url, String bearer) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(20_000);
        connection.setReadTimeout(35_000);
        connection.setRequestMethod("GET");
        connection.setInstanceFollowRedirects(false);
        connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "image/jpeg");
        if (bearer != null && !bearer.isEmpty()) {
            connection.setRequestProperty("Authorization", "Bearer " + bearer);
        }
        int status = connection.getResponseCode();
        String contentType = String.valueOf(connection.getContentType());
        InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
        byte[] responseBody = stream == null ? new byte[0] : readBoundedBytes(stream, 10 * 1024 * 1024);
        connection.disconnect();
        return new BinaryHttpResult(status, contentType, responseBody);
    }

    private static String readSmall(InputStream input) throws Exception {
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192];
            int total = 0;
            int count;
            while ((count = stream.read(buffer)) >= 0) {
                total += count;
                if (total > 2 * 1024 * 1024) {
                    throw new IllegalStateException("response too large");
                }
                output.write(buffer, 0, count);
            }
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static byte[] readBoundedBytes(InputStream input, int maximumBytes) throws Exception {
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[16_384];
            int total = 0;
            int count;
            while ((count = stream.read(buffer)) >= 0) {
                total += count;
                if (total > maximumBytes) throw new IllegalStateException("response too large");
                output.write(buffer, 0, count);
            }
            return output.toByteArray();
        }
    }

    private void clearSession() {
        processAccessToken = "";
        processTokenExpiresAt = 0L;
    }

    private static String responseErrorCode(String body) {
        if (body == null || body.isEmpty()) return "";
        try {
            return new JSONObject(body).optString("error", "").trim();
        } catch (Exception ignored) {
            return "";
        }
    }

    private static String friendlyFailure(int status, String code) {
        if (status == 403) return "Tailscale 연결과 소유자 로그인을 확인해 주세요.";
        if (status == 401) return "이 기기의 소유자 인증을 갱신할 수 없습니다.";
        if (status == 428) {
            if ("location_prefetch_scope_mismatch".equals(code)) {
                return "GPS를 확인한 날짜와 원래 분석 날짜가 달라 재분석을 시작하지 않았습니다. "
                        + "앱을 최신 버전으로 업데이트한 뒤 다시 실행해 주세요.";
            }
            if ("location_prefetch_not_received".equals(code)) {
                return "휴대폰의 GPS 전송은 끝났지만 Mac에서 아직 모두 확인되지 않았습니다. "
                        + "잠시 후 다시 실행해 주세요.";
            }
            if ("location_prefetch_stale".equals(code)) {
                return "GPS 확인 완료 후 시간이 지나 영수증이 만료됐습니다. 다시 실행해 주세요.";
            }
            if ("location_prefetch_required".equals(code)) {
                return "이 재분석에는 선택 날짜의 GPS 선동기화가 필요합니다. "
                        + "앱을 최신 버전으로 업데이트한 뒤 다시 실행해 주세요.";
            }
            if ("location_prefetch_count_invalid".equals(code)) {
                return "휴대폰의 사진 조회 수와 GPS 수가 맞지 않아 재분석을 시작하지 않았습니다.";
            }
            if ("location_prefetch_time_invalid".equals(code)
                    || "location_prefetch_scope_invalid".equals(code)) {
                return "GPS 동기화 확인 정보가 올바르지 않아 재분석을 시작하지 않았습니다.";
            }
            return "선택한 날짜의 GPS 동기화 확인이 필요합니다. 다시 실행해 주세요.";
        }
        if (status >= 500) return "Mac의 PhotosMcp 서비스가 아직 준비되지 않았습니다.";
        return "PhotosMcp에 연결하지 못했습니다.";
    }

    private SharedPreferences prefs() {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static void requireSafeId(String value) {
        if (value == null || !value.matches("[A-Za-z0-9._:-]{8,160}")) {
            throw new IllegalArgumentException("invalid identifier");
        }
    }

    static final class WebBootstrap {
        final String url;
        final String exchangeCode;

        WebBootstrap(String url, String exchangeCode) {
            this.url = url;
            this.exchangeCode = exchangeCode;
        }

        byte[] postBody() {
            // Exchange codes are URL-safe already. Avoid adding a JavaScript bridge or bearer token.
            return ("exchange_code=" + exchangeCode).getBytes(StandardCharsets.UTF_8);
        }
    }

    static final class OwnerApiException extends Exception {
        final int status;
        final String code;

        OwnerApiException(int status, String code, String message) {
            super(message);
            this.status = status;
            this.code = code == null ? "" : code;
        }
    }

    private static final class HttpResult {
        final int status;
        final String body;

        HttpResult(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    private static final class BinaryHttpResult {
        final int status;
        final String contentType;
        final byte[] body;

        BinaryHttpResult(int status, String contentType, byte[] body) {
            this.status = status;
            this.contentType = contentType == null ? "" : contentType;
            this.body = body;
        }
    }
}
