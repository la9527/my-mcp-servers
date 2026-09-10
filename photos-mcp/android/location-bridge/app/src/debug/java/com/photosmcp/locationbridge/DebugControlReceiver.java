package com.photosmcp.locationbridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** ADB-only validation hook; this source set is absent from release APKs. */
public final class DebugControlReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        if ("com.photosmcp.locationbridge.DEBUG_SCHEDULE".equals(intent.getAction())) {
            SyncJobService.schedule(context);
            return;
        }
        if (!"com.photosmcp.locationbridge.DEBUG_SYNC".equals(intent.getAction())) return;
        PendingResult pending = goAsync();
        ExecutorService executor = Executors.newSingleThreadExecutor();
        executor.execute(() -> {
            try {
                BridgeSync.run(context);
            } catch (Exception ignored) {
                // The app status and encrypted outbox retain the outcome without private log data.
            } finally {
                pending.finish();
                executor.shutdown();
            }
        });
    }
}
