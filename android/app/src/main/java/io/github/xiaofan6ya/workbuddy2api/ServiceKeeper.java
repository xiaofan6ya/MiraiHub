package io.github.xiaofan6ya.workbuddy2api;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.SystemClock;

/**
 * 前台服务保活：精确闹钟看门狗 + 用户意图持久化。
 *
 * 背景（issue #37，ColorOS 17 / Android 16）：
 *   国产 ROM 会在「应用切后台」后冻结甚至回收进程，`START_STICKY` 只保证
 *   「系统愿意的话」会重建服务 —— 被冻结时根本走不到 onStartCommand。
 *   唯一能在应用完全死掉后把服务拉回来的机制是 **AlarmManager 的精确闹钟**
 *   配 `PendingIntent.getForegroundService()`：闹钟由系统持有，哪怕进程被杀，
 *   到点仍会拉起前台服务（force-stop 除外，那属于用户明确拒绝）。
 *
 * 同时把「用户是否主动停止服务」落盘：主动停止 = 不要看门狗再拉起来，
 * 否则用户点了「停止服务」，几分钟后通知又自己冒出来，属于耍流氓。
 */
final class ServiceKeeper {
    static final String ACTION_WATCHDOG = "io.github.xiaofan6ya.workbuddy2api.WATCHDOG";

    /** 服务活着时的心跳周期：只做「还活着吗」的复核，不必很密。 */
    private static final long HEARTBEAT_MS = 10 * 60 * 1000L;
    /** 服务被回收 / 任务被划掉后的快速重试：1 分钟，最多连续 5 次。 */
    private static final long RETRY_MS = 60 * 1000L;
    private static final int RETRY_LIMIT = 5;

    private static final String PREFS = "local_api_service";
    private static final String KEY_ENABLED = "enabled";
    private static final String KEY_RETRIES = "retries";
    private static final String KEY_INTERRUPTS = "interrupts";
    private static final String KEY_LAST_INTERRUPT = "last_interrupt";

    private ServiceKeeper() {}

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** 默认开启：装完即后台可用，是这类「本机网关」的产品前提。 */
    static boolean enabled(Context context) {
        return prefs(context).getBoolean(KEY_ENABLED, true);
    }

    static void setEnabled(Context context, boolean enabled) {
        prefs(context).edit().putBoolean(KEY_ENABLED, enabled).apply();
        if (!enabled) cancel(context);
    }

    /* ── 被打断的可观测性 ──────────────────────────────────────────────── */

    static void noteInterrupt(Context context, String reason) {
        SharedPreferences p = prefs(context);
        p.edit()
            .putInt(KEY_INTERRUPTS, p.getInt(KEY_INTERRUPTS, 0) + 1)
            .putString(KEY_LAST_INTERRUPT, reason == null ? "" : reason)
            .apply();
    }

    static int interrupts(Context context) {
        return prefs(context).getInt(KEY_INTERRUPTS, 0);
    }

    static String lastInterrupt(Context context) {
        return prefs(context).getString(KEY_LAST_INTERRUPT, "");
    }

    /* ── 闹钟 ──────────────────────────────────────────────────────────── */

    static void scheduleHeartbeat(Context context) {
        prefs(context).edit().putInt(KEY_RETRIES, 0).apply();
        schedule(context, HEARTBEAT_MS);
    }

    /**
     * 服务刚被回收时用短周期连续拉几次；超过 RETRY_LIMIT 退回心跳周期，
     * 避免某个永远起不来的环境每 60 秒把设备唤醒一次。
     */
    static void scheduleRetry(Context context) {
        SharedPreferences p = prefs(context);
        int retries = p.getInt(KEY_RETRIES, 0);
        if (!enabled(context)) return;
        if (retries >= RETRY_LIMIT) {
            p.edit().putInt(KEY_RETRIES, 0).apply();
            schedule(context, HEARTBEAT_MS);
            return;
        }
        p.edit().putInt(KEY_RETRIES, retries + 1).apply();
        schedule(context, RETRY_MS);
    }

    static void cancel(Context context) {
        AlarmManager manager = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (manager != null) {
            try { manager.cancel(pending(context)); } catch (Exception ignored) { }
        }
        prefs(context).edit().putInt(KEY_RETRIES, 0).apply();
    }

    private static void schedule(Context context, long delayMs) {
        if (!enabled(context)) return;
        AlarmManager manager = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        if (manager == null) return;
        PendingIntent intent = pending(context);
        long at = SystemClock.elapsedRealtime() + delayMs;
        try {
            if (Build.VERSION.SDK_INT >= 23 && exactAllowed(manager)) {
                // 精确闹钟能在 Doze / 冻结后照常唤醒，这是保活的关键；
                // wakeup 型保证设备休眠时也会醒来。
                manager.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent);
            } else if (Build.VERSION.SDK_INT >= 23) {
                manager.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent);
            } else {
                manager.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent);
            }
        } catch (SecurityException denied) {
            // Android 12+ 未授予 SCHEDULE_EXACT_ALARM：降级成不精确的 idle 闹钟，
            // 晚一两分钟但不会抛异常打断 onStartCommand。
            try {
                if (Build.VERSION.SDK_INT >= 23) {
                    manager.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent);
                } else {
                    manager.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent);
                }
            } catch (Exception ignored) { }
        } catch (Exception ignored) { }
    }

    private static boolean exactAllowed(AlarmManager manager) {
        if (Build.VERSION.SDK_INT < 31) return true;
        try { return manager.canScheduleExactAlarms(); } catch (Exception e) { return false; }
    }

    private static PendingIntent pending(Context context) {
        Intent intent = new Intent(context, LocalApiService.class).setAction(ACTION_WATCHDOG);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        if (Build.VERSION.SDK_INT >= 26) return PendingIntent.getForegroundService(context, 7, intent, flags);
        return PendingIntent.getService(context, 7, intent, flags);
    }
}