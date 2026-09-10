package com.photosmcp.locationbridge;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Iterator;
import java.util.UUID;

final class ApiClient {
    private static final String PREFS = "bridge";
    private static final String SIGNED_PATH = "/mobile-location/v1/batches";
    private final Context context;

    ApiClient(Context context) {
        this.context = context.getApplicationContext();
    }

    boolean isEnrolled() {
        SharedPreferences prefs = prefs();
        return !prefs.getString("server_base", "").isEmpty()
                && !prefs.getString("device_id", "").isEmpty()
                && !prefs.getString("key_id", "").isEmpty();
    }

    void enroll(String enrollmentJson) throws Exception {
        JSONObject source = new JSONObject(enrollmentJson);
        String base = normalizedHttpsBase(source.getString("server_base_url"));
        String token = source.getString("enrollment_token");
        JSONObject request = new JSONObject();
        request.put("schema_version", 1);
        request.put("token", token);
        request.put("public_key_pem", BridgeKeys.publicKeyPem());
        request.put("device_label", android.os.Build.MODEL);
        request.put("attestation_status", "unverified");
        HttpResult response = post(base + "/v1/enroll",
                request.toString().getBytes(StandardCharsets.UTF_8), null);
        if (response.status != 201) throw new IllegalStateException("등록이 거부되었습니다");
        JSONObject result = new JSONObject(response.body);
        prefs().edit()
                .putString("server_base", base)
                .putString("device_id", result.getString("device_id"))
                .putString("key_id", result.getString("key_id"))
                .commit();
    }

    boolean send(OutboxDb.Batch batch) throws Exception {
        SharedPreferences prefs = prefs();
        String base = normalizedHttpsBase(prefs.getString("server_base", ""));
        byte[] body = batch.payload.getBytes(StandardCharsets.UTF_8);
        String sentAt = Instant.now().toString();
        String nonce = Base64.encodeToString(randomBytes(24),
                Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
        String bodySha = BridgeKeys.hex(MessageDigest.getInstance("SHA-256").digest(body));
        String canonical = "POST\n" + SIGNED_PATH + "\n" + bodySha + "\n" + sentAt
                + "\n" + nonce + "\n" + batch.sequence + "\n" + batch.idempotencyKey;
        JSONObject headers = new JSONObject();
        headers.put("X-Photos-Device-Id", prefs.getString("device_id", ""));
        headers.put("X-Photos-Key-Id", prefs.getString("key_id", ""));
        headers.put("X-Photos-Sent-At", sentAt);
        headers.put("X-Photos-Nonce", nonce);
        headers.put("X-Photos-Sequence", Long.toString(batch.sequence));
        headers.put("Idempotency-Key", batch.idempotencyKey);
        headers.put("X-Photos-Signature",
                BridgeKeys.sign(canonical.getBytes(StandardCharsets.UTF_8)));
        HttpResult response = post(base + "/v1/batches", body, headers);
        return response.status == 200;
    }

    private HttpResult post(String url, byte[] body, JSONObject headers) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(20_000);
        connection.setReadTimeout(30_000);
        connection.setRequestMethod("POST");
        connection.setDoOutput(true);
        connection.setInstanceFollowRedirects(false);
        connection.setFixedLengthStreamingMode(body.length);
        connection.setRequestProperty("Content-Type", "application/json");
        if (headers != null) {
            Iterator<String> names = headers.keys();
            while (names.hasNext()) {
                String name = names.next();
                connection.setRequestProperty(name, headers.getString(name));
            }
        }
        try (OutputStream output = connection.getOutputStream()) {
            output.write(body);
        }
        int status = connection.getResponseCode();
        InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
        String responseBody = stream == null ? "{}" : readSmall(stream);
        connection.disconnect();
        return new HttpResult(status, responseBody);
    }

    private static String readSmall(InputStream input) throws Exception {
        try (InputStream stream = input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int total = 0;
            int count;
            while ((count = stream.read(buffer)) >= 0) {
                total += count;
                if (total > 65_536) throw new IllegalStateException("response too large");
                output.write(buffer, 0, count);
            }
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static String normalizedHttpsBase(String input) {
        String value = input == null ? "" : input.trim();
        if (!value.startsWith("https://") || !value.endsWith("/mobile-location")) {
            throw new IllegalArgumentException("HTTPS PhotosMcp 수신 주소만 허용됩니다");
        }
        return value.substring(0, value.length() - (value.endsWith("/") ? 1 : 0));
    }

    private static byte[] randomBytes(int size) {
        byte[] value = new byte[size];
        new java.security.SecureRandom().nextBytes(value);
        return value;
    }

    private SharedPreferences prefs() {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static final class HttpResult {
        final int status;
        final String body;
        HttpResult(int status, String body) { this.status = status; this.body = body; }
    }
}
