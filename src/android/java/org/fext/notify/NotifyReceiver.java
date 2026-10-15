package org.fext.notify;

import android.app.RemoteInput;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.util.Log;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Method;
import java.nio.charset.Charset;

/**
 * What is done to a notification from the shade: a reply typed into it,
 * Mark as read, or Decline on a call. Each is written as one small JSON
 * file in the app's .fext/commands folder, for whichever sync worker is
 * running to act on (main.MessagingMixin.run_commands); the background
 * sync service is started in case none is. The notification goes at once.
 */
public class NotifyReceiver extends BroadcastReceiver {
    private static final String TAG = "fext-notify";

    @Override
    public void onReceive(Context ctx, Intent intent) {
        String action = intent.getAction();
        String peer = intent.getStringExtra("peer");
        int id = intent.getIntExtra("id", 0);
        if (action == null || peer == null) {
            return;
        }
        try {
            JSONObject cmd = new JSONObject();
            cmd.put("peer", peer);
            String me = intent.getStringExtra("me");
            cmd.put("me", me == null ? "" : me);
            cmd.put("at", System.currentTimeMillis());
            if (Notifier.ACTION_REPLY.equals(action)) {
                Bundle typed = RemoteInput.getResultsFromIntent(intent);
                CharSequence text = typed == null ? null
                        : typed.getCharSequence(Notifier.KEY_REPLY);
                if (text == null || text.toString().trim().isEmpty()) {
                    Notifier.cancel(ctx, id);
                    return;
                }
                cmd.put("op", "reply");
                cmd.put("text", text.toString());
            } else if (Notifier.ACTION_READ.equals(action)) {
                cmd.put("op", "read");
            } else if (Notifier.ACTION_DECLINE.equals(action)) {
                cmd.put("op", "decline");
                cmd.put("call", intent.getStringExtra("call"));
            } else {
                return;
            }
            write(ctx, cmd);
        } catch (Exception e) {
            Log.w(TAG, "could not keep a notification's action", e);
        }
        Notifier.cancel(ctx, id);
        startSync(ctx);
    }

    /** Written whole, then renamed into place: never read half-written. */
    private static void write(Context ctx, JSONObject cmd) throws Exception {
        File dir = new File(ctx.getFilesDir(), ".fext/commands");
        if (!dir.isDirectory() && !dir.mkdirs()) {
            throw new IllegalStateException("no commands folder");
        }
        String name = System.currentTimeMillis() + "-"
                + Integer.toHexString((int) (Math.random() * 0x7fffffff))
                + ".json";
        File tmp = new File(dir, name + ".tmp");
        FileOutputStream out = new FileOutputStream(tmp);
        try {
            out.write(cmd.toString().getBytes(Charset.forName("UTF-8")));
        } finally {
            out.close();
        }
        if (!tmp.renameTo(new File(dir, name))) {
            throw new IllegalStateException("could not place " + name);
        }
    }

    /** The background sync service, if it is not running already (a
     * notification's action may start one). */
    private static void startSync(Context ctx) {
        try {
            Class<?> service = Class.forName(
                    ctx.getPackageName() + ".ServiceFextsync");
            Method start = service.getMethod("start", Context.class,
                    String.class, String.class, String.class, String.class);
            start.invoke(null, ctx, "ic_stat_ferritext", "Ferritext",
                    "Ready for new messages", "");
        } catch (Exception e) {
            Log.w(TAG, "could not start the sync service", e);
        }
    }
}
