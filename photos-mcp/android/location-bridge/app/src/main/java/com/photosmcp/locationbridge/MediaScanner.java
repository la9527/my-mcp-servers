package com.photosmcp.locationbridge;

import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.provider.MediaStore;

import androidx.exifinterface.media.ExifInterface;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

final class MediaScanner {
    private static final int MAX_SCAN = 1000;
    private static final int BATCH_SIZE = 100;
    private final Context context;

    MediaScanner(Context context) {
        this.context = context.getApplicationContext();
    }

    int scanToOutbox(OutboxDb outbox) throws Exception {
        SharedPreferences prefs = context.getSharedPreferences("bridge", Context.MODE_PRIVATE);
        long defaultStart = Instant.now().minusSeconds(10L * 24 * 60 * 60).getEpochSecond();
        long checkpoint = prefs.getLong("media_checkpoint", defaultStart);
        long checkpointId = prefs.getLong("media_checkpoint_id", -1);
        ContentResolver resolver = context.getContentResolver();
        Uri collection = Build.VERSION.SDK_INT >= 29
                ? MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL)
                : MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
        String[] projection = {
                MediaStore.Images.Media._ID,
                MediaStore.Images.Media.DATE_ADDED,
                MediaStore.Images.Media.DATE_TAKEN,
                MediaStore.Images.Media.WIDTH,
                MediaStore.Images.Media.HEIGHT,
                MediaStore.Images.Media.MIME_TYPE,
                MediaStore.Images.Media.GENERATION_MODIFIED
        };
        List<JSONObject> manifests = new ArrayList<>();
        long maxSeen = checkpoint;
        long maxSeenId = checkpointId;
        int visited = 0;
        String selection = MediaStore.Images.Media.DATE_ADDED + " >= ?";
        String[] selectionArgs = new String[]{Long.toString(checkpoint)};
        if (Build.VERSION.SDK_INT >= 29) {
            selection += " AND " + MediaStore.Images.Media.RELATIVE_PATH + " LIKE ?";
            selectionArgs = new String[]{Long.toString(checkpoint), "DCIM/Camera/%"};
        }
        try (Cursor cursor = resolver.query(collection, projection,
                selection,
                selectionArgs,
                MediaStore.Images.Media.DATE_ADDED + " ASC," + MediaStore.Images.Media._ID + " ASC")) {
            if (cursor == null) return 0;
            while (cursor.moveToNext() && visited < MAX_SCAN) {
                visited++;
                long id = cursor.getLong(0);
                long dateAdded = cursor.getLong(1);
                if (dateAdded == checkpoint && id <= checkpointId) continue;
                long dateTaken = cursor.isNull(2) ? dateAdded * 1000 : cursor.getLong(2);
                int width = Math.max(1, cursor.getInt(3));
                int height = Math.max(1, cursor.getInt(4));
                String mime = cursor.getString(5);
                long generation = cursor.getLong(6);
                if (dateAdded > maxSeen || (dateAdded == maxSeen && id > maxSeenId)) {
                    maxSeen = dateAdded;
                    maxSeenId = id;
                }
                Uri uri = ContentUris.withAppendedId(collection, id);
                Uri original = Build.VERSION.SDK_INT >= 29 ? MediaStore.setRequireOriginal(uri) : uri;
                try {
                    float[] latLong = new float[2];
                    boolean hasLocation;
                    try (InputStream input = resolver.openInputStream(original)) {
                        if (input == null) continue;
                        hasLocation = new ExifInterface(input).getLatLong(latLong);
                    }
                    if (!hasLocation) continue;
                    String strongDigest;
                    try (InputStream input = resolver.openInputStream(original)) {
                        if (input == null) continue;
                        MessageDigest digest = MessageDigest.getInstance("SHA-256");
                        byte[] buffer = new byte[128 * 1024];
                        int count;
                        while ((count = input.read(buffer)) >= 0) digest.update(buffer, 0, count);
                        strongDigest = BridgeKeys.hex(digest.digest());
                    }
                    JSONObject item = new JSONObject();
                    item.put("device_asset_key", BridgeKeys.assetKey(id + ":" + generation + ":" + dateAdded));
                    item.put("captured_at", Instant.ofEpochMilli(dateTaken).toString());
                    item.put("width", width);
                    item.put("height", height);
                    item.put("mime_type", normalizeMime(mime));
                    item.put("strong_content_digest", strongDigest);
                    item.put("perceptual_hash", "");
                    item.put("latitude", latLong[0]);
                    item.put("longitude", latLong[1]);
                    item.put("extractor_version", "android-bridge-1");
                    manifests.add(item);
                } catch (SecurityException denied) {
                    throw new SecurityException("사진의 원본 위치 접근 권한이 필요합니다");
                } catch (Exception unreadableAsset) {
                    // A single damaged or unsupported image must not block later assets.
                }
            }
        }
        int queued = 0;
        for (int offset = 0; offset < manifests.size(); offset += BATCH_SIZE) {
            JSONArray items = new JSONArray();
            for (int index = offset; index < Math.min(offset + BATCH_SIZE, manifests.size()); index++) {
                items.put(manifests.get(index));
            }
            JSONObject payload = new JSONObject();
            payload.put("schema_version", 1);
            payload.put("manifests", items);
            long sequence = prefs.getLong("next_sequence", 1);
            outbox.enqueue(sequence, payload.toString());
            if (!prefs.edit().putLong("next_sequence", sequence + 1).commit()) {
                throw new IllegalStateException("sequence checkpoint failed");
            }
            queued += items.length();
        }
        if (visited > 0) {
            prefs.edit()
                    .putLong("media_checkpoint", maxSeen)
                    .putLong("media_checkpoint_id", maxSeenId)
                    .commit();
        }
        return queued;
    }

    private static String normalizeMime(String value) {
        if (value == null) return "image/jpeg";
        String normalized = value.toLowerCase(Locale.ROOT);
        switch (normalized) {
            case "image/heic":
            case "image/heif":
            case "image/png":
            case "image/webp":
            case "image/jpeg": return normalized;
            default: return "image/jpeg";
        }
    }
}
