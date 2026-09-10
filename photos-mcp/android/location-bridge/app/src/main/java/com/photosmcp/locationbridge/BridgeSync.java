package com.photosmcp.locationbridge;

import android.content.Context;
import android.content.SharedPreferences;

final class BridgeSync {
    static final class Result {
        final int queued;
        final int delivered;
        final int remaining;
        Result(int queued, int delivered, int remaining) {
            this.queued = queued;
            this.delivered = delivered;
            this.remaining = remaining;
        }
    }

    private BridgeSync() {}

    static Result run(Context context) throws Exception {
        SharedPreferences prefs = context.getSharedPreferences("bridge", Context.MODE_PRIVATE);
        prefs.edit().putString("sync_state", "running").remove("last_error_class").commit();
        try {
            ApiClient api = new ApiClient(context);
            if (!api.isEnrolled()) throw new IllegalStateException("먼저 Mac 등록 정보를 붙여넣어 등록하세요");
            OutboxDb outbox = new OutboxDb(context);
            try {
                int queued = new MediaScanner(context).scanToOutbox(outbox);
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
                return new Result(queued, delivered, remaining);
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
}
