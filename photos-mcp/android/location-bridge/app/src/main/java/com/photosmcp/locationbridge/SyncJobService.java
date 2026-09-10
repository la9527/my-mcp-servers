package com.photosmcp.locationbridge;

import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class SyncJobService extends JobService {
    private static final int JOB_ID = 710_931;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    static int schedule(Context context) {
        JobScheduler scheduler = context.getSystemService(JobScheduler.class);
        JobInfo info = new JobInfo.Builder(JOB_ID,
                new ComponentName(context, SyncJobService.class))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPersisted(true)
                .setPeriodic(24L * 60 * 60 * 1000)
                .setBackoffCriteria(15L * 60 * 1000, JobInfo.BACKOFF_POLICY_EXPONENTIAL)
                .build();
        int result = scheduler.schedule(info);
        context.getSharedPreferences("bridge", Context.MODE_PRIVATE)
                .edit().putInt("job_schedule_result", result).commit();
        return result;
    }

    @Override public boolean onStartJob(JobParameters params) {
        executor.execute(() -> {
            boolean retry = false;
            try {
                BridgeSync.Result result = BridgeSync.run(getApplicationContext());
                retry = result.remaining > 0;
            } catch (Exception ignored) {
                // No photo names, coordinates, tokens, or request bodies are logged.
                retry = true;
            }
            jobFinished(params, retry);
        });
        return true;
    }

    @Override public boolean onStopJob(JobParameters params) {
        return true;
    }

    @Override public void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }
}
