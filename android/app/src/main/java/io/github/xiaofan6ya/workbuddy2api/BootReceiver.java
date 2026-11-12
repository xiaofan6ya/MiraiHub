package io.github.xiaofan6ya.workbuddy2api;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/**
 * 开机 / 应用更新后重启本地 API 服务。
 *
 * 为什么必须要有：前面那条「前台服务常驻」只覆盖「进程还活着」。手机重启、
 * ROM 升级、应用覆盖安装都会把进程清掉 —— 用户不打开 App，网关就一直不监听，
 * 别的设备上的客户端会全部连不上。这个接收器把「用户点过启动」这件事
 * 跨重启记住（ServiceKeeper 的 enabled 落盘），开机后自己把服务拉起来。
 */
public class BootReceiver extends BroadcastReceiver {
    private static final String[] ACTIONS = new String[]{
        Intent.ACTION_BOOT_COMPLETED,
        Intent.ACTION_LOCKED_BOOT_COMPLETED,
        Intent.ACTION_MY_PACKAGE_REPLACED,
        "android.intent.action.QUICKBOOT_POWERON",
        "com.htc.intent.action.QUICKBOOT_POWERON",
        "com.oppo.action.QUICKBOOT_POWERON",
        "android.intent.action.USER_UNLOCKED",
    };

    @Override public void onReceive(Context context, Intent intent) {
        String action = intent == null ? "" : String.valueOf(intent.getAction());
        boolean known = false;
        for (String candidate : ACTIONS) if (candidate.equals(action)) { known = true; break; }
        if (!known) return;
        // 用户主动停过服务就别自作聪明；enabled 默认 true（首次安装即常驻）。
        if (!ServiceKeeper.enabled(context)) return;
        ServiceKeeper.scheduleHeartbeat(context);
        Intent service = new Intent(context, LocalApiService.class);
        try {
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(service);
            else context.startService(service);
        } catch (Exception e) {
            // 开机限制（如 Android 15+ 对部分 FGS 类型的额外约束）不该静默失败，
            // 落一条记录，设置页能看到「最近一次被拦截的原因」。
            ServiceKeeper.noteInterrupt(context, "系统拒绝开机自启：" + e.getClass().getSimpleName());
        }
    }
}