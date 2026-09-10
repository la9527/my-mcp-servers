package com.photosmcp.locationbridge;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.util.LruCache;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.Comparator;

/** Private, bounded cache for metadata-free recommendation derivatives. */
final class PrivateImageCache {
    private static final int MAX_ENCODED_BYTES = 10 * 1024 * 1024;
    private static final long MAX_DISK_BYTES = 128L * 1024L * 1024L;
    private final File root;
    private final LruCache<String, Bitmap> memory;

    PrivateImageCache(Context context) {
        root = new File(context.getCacheDir(), "recommendation-images-v1");
        if (!root.exists()) root.mkdirs();
        memory = new LruCache<>(24 * 1024) {
            @Override protected int sizeOf(String key, Bitmap value) {
                return Math.max(1, value.getAllocationByteCount() / 1024);
            }
        };
    }

    Bitmap load(OwnerApiClient api, String assetId, String kind, int targetPixels)
            throws Exception {
        return load(api, assetId, kind, targetPixels, "");
    }

    Bitmap load(OwnerApiClient api, String assetId, String kind, int targetPixels, String storyId)
            throws Exception {
        String scope = storyId == null ? "" : storyId;
        String cacheKey = scope + ":" + assetId + ":" + kind + ":" + targetPixels;
        synchronized (memory) {
            Bitmap cached = memory.get(cacheKey);
            if (cached != null && !cached.isRecycled()) return cached;
        }
        File encodedFile = new File(root, digest(scope + "\n" + assetId + "\n" + kind) + ".jpg");
        byte[] encoded = readCacheFile(encodedFile);
        if (encoded == null) {
            encoded = api.getResultImage(assetId, kind, scope);
            writeCacheFile(encodedFile, encoded);
            pruneDiskCache();
        }
        Bitmap bitmap = decode(encoded, targetPixels);
        if (bitmap == null) {
            encodedFile.delete();
            throw new IllegalStateException("recommendation image could not be decoded");
        }
        encodedFile.setLastModified(System.currentTimeMillis());
        synchronized (memory) {
            memory.put(cacheKey, bitmap);
        }
        return bitmap;
    }

    private static Bitmap decode(byte[] encoded, int targetPixels) {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(encoded, 0, encoded.length, bounds);
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null;
        int sample = 1;
        int safeTarget = Math.max(240, targetPixels);
        while (bounds.outWidth / (sample * 2) >= safeTarget
                && bounds.outHeight / (sample * 2) >= safeTarget) {
            sample *= 2;
        }
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = sample;
        options.inPreferredConfig = Bitmap.Config.RGB_565;
        options.inDither = true;
        return BitmapFactory.decodeByteArray(encoded, 0, encoded.length, options);
    }

    private static byte[] readCacheFile(File file) {
        if (!file.isFile() || file.length() <= 0 || file.length() > MAX_ENCODED_BYTES) {
            return null;
        }
        try (FileInputStream input = new FileInputStream(file);
             ByteArrayOutputStream output = new ByteArrayOutputStream((int) file.length())) {
            byte[] buffer = new byte[16_384];
            int total = 0;
            int count;
            while ((count = input.read(buffer)) >= 0) {
                total += count;
                if (total > MAX_ENCODED_BYTES) return null;
                output.write(buffer, 0, count);
            }
            return output.toByteArray();
        } catch (Exception ignored) {
            return null;
        }
    }

    private static void writeCacheFile(File destination, byte[] encoded) {
        if (encoded.length == 0 || encoded.length > MAX_ENCODED_BYTES) return;
        File temporary = new File(destination.getParentFile(), destination.getName() + ".tmp");
        try (FileOutputStream output = new FileOutputStream(temporary)) {
            output.write(encoded);
            output.getFD().sync();
            if (!temporary.renameTo(destination)) temporary.delete();
        } catch (Exception ignored) {
            temporary.delete();
        }
    }

    private void pruneDiskCache() {
        File[] files = root.listFiles(file -> file.isFile() && file.getName().endsWith(".jpg"));
        if (files == null) return;
        long total = 0;
        for (File file : files) total += file.length();
        if (total <= MAX_DISK_BYTES) return;
        Arrays.sort(files, Comparator.comparingLong(File::lastModified));
        for (File file : files) {
            long size = file.length();
            if (file.delete()) total -= size;
            if (total <= MAX_DISK_BYTES) break;
        }
    }

    private static String digest(String value) throws Exception {
        byte[] bytes = MessageDigest.getInstance("SHA-256")
                .digest(value.getBytes(StandardCharsets.UTF_8));
        StringBuilder encoded = new StringBuilder(bytes.length * 2);
        final char[] hexadecimal = "0123456789abcdef".toCharArray();
        for (byte item : bytes) {
            int unsigned = item & 0xff;
            encoded.append(hexadecimal[unsigned >>> 4]);
            encoded.append(hexadecimal[unsigned & 0x0f]);
        }
        return encoded.toString();
    }
}
