package io.github.xiaofan6ya.workbuddy2api;

import android.app.Activity;
import android.os.Bundle;
import android.util.Log;
import com.chaquo.python.Python;
import java.io.File;

/** Runs developer probes inside the actual embedded Python runtime, debug APK only. */
public class DebugProbeActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        String name = getIntent().getStringExtra("script");
        new Thread(() -> {
            try {
                File script = new File(getFilesDir(), name).getCanonicalFile();
                if (!script.getParentFile().equals(getFilesDir().getCanonicalFile()))
                    throw new IllegalArgumentException("Probe must be in app files");
                Python.getInstance().getModule("runpy").callAttr("run_path", script.toString());
                Log.i("WorkBuddyProbe", "Probe completed");
            } catch (Exception e) { Log.e("WorkBuddyProbe", "Probe failed", e); }
        }, "workbuddy-debug-probe").start();
        finish();
    }
}
