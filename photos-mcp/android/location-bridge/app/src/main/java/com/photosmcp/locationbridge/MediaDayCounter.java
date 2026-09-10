package com.photosmcp.locationbridge;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.os.Build;
import android.provider.MediaStore;

import java.time.LocalDate;
import java.time.ZoneId;

/** Advisory, metadata-only MediaStore count for the manual Story form. */
final class MediaDayCounter {
    private MediaDayCounter() {}

    static Result count(Context context, LocalDate from, LocalDate to) {
        boolean fullPermission = Build.VERSION.SDK_INT < 33
                ? context.checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE)
                        == PackageManager.PERMISSION_GRANTED
                : context.checkSelfPermission(Manifest.permission.READ_MEDIA_IMAGES)
                        == PackageManager.PERMISSION_GRANTED;
        boolean partialPermission = Build.VERSION.SDK_INT >= 34
                && context.checkSelfPermission(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED)
                        == PackageManager.PERMISSION_GRANTED;
        if (!fullPermission && !partialPermission) return new Result(0, "permission_required");
        ZoneId seoul = ZoneId.of("Asia/Seoul");
        long startMillis = from.atStartOfDay(seoul).toInstant().toEpochMilli();
        long endMillis = to.plusDays(1).atStartOfDay(seoul).toInstant().toEpochMilli();
        String selection = MediaStore.Images.Media.DATE_TAKEN + " >= ? AND "
                + MediaStore.Images.Media.DATE_TAKEN + " < ?";
        String[] arguments = {String.valueOf(startMillis), String.valueOf(endMillis)};
        int count = 0;
        try (Cursor cursor = context.getContentResolver().query(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                new String[]{MediaStore.Images.Media._ID},
                selection,
                arguments,
                null)) {
            if (cursor != null) count = cursor.getCount();
        }
        return new Result(count, fullPermission ? "exact_local" : "partial_permission");
    }

    static final class Result {
        final int count;
        final String status;

        Result(int count, String status) {
            this.count = Math.max(0, count);
            this.status = status;
        }
    }
}
