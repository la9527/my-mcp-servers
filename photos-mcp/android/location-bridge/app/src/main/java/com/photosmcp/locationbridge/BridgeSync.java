package com.photosmcp.locationbridge;

import android.content.Context;
import android.content.SharedPreferences;

import java.time.LocalDate;

final class BridgeSync {
    static final class Result {
        final int queued;
        final int delivered;
        final int remaining;
        final int scanned;
        Result(int queued, int delivered, int remaining, int scanned) {
            this.queued = queued;
            this.delivered = delivered;
            this.remaining = remaining;
            this.scanned = scanned;
        }
    }

    private BridgeSync() {}

    static synchronized Result run(Context context) throws Exception {
        return execute(context, outbox -> new MediaScanner(context).scanToOutbox(outbox), -1);
    }

    static synchronized Result runRange(Context context, LocalDate from, LocalDate to) throws Exception {
        Result result = execute(
                context,
                outbox -> new MediaScanner(context).scanRangeToOutbox(outbox, from, to),
                0);
        int scanned = context.getSharedPreferences("bridge", Context.MODE_PRIVATE)
                .getInt("last_manual_range_scanned", 0);
        return new Result(result.queued, result.delivered, result.remaining, scanned);
    }

    private static Result execute(Context context, ScanAction scan, int scanned) throws Exception {
        SharedPreferences prefs = context.getSharedPreferences("bridge", Context.MODE_PRIVATE);
        prefs.edit().putString("sync_state", "running").remove("last_error_class").commit();
        try {
            ApiClient api = new ApiClient(context);
            if (!api.isEnrolled()) throw new IllegalStateException("먼저 Mac 등록 정보를 붙여넣어 등록하세요");
            OutboxDb outbox = new OutboxDb(context);
            try {
                int queued = scan.run(outbox);
                int delivered = 0;
                for (OutboxDb.Batch batch : outbox.pending()) {
                    if (!api.send(batch)) break;
                    outbox.acknowledge(batch.rowId);
                    delivered++;
                }
                int remaining = outbox.count();
                prefs.edit()
                        .putString("sync_state", remaining == 0 ? "success" : "pending")
                        .putLong("last_sync_at", System.currentTimeMillis())
                        .putInt("last_queued", queued)
                        .putInt("last_delivered_batches", delivered)
                        .putInt("last_remaining_batches", remaining)
                        .commit();
                return new Result(queued, delivered, remaining, scanned);
            } finally {
                outbox.close();
            }
        } catch (Exception failure) {
            prefs.edit()
                    .putString("sync_state", "failed")
                    .putString("last_error_class", failure.getClass().getSimpleName())
                    .commit();
            throw failure;
        }
    }

    private interface ScanAction {
        int run(OutboxDb outbox) throws Exception;
    }
}
