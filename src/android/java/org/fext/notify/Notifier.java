package org.fext.notify;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Person;
import android.app.RemoteInput;
import android.content.Context;
import android.content.Intent;
import android.graphics.drawable.Icon;
import android.media.AudioAttributes;
import android.media.AudioManager;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;

/**
 * The app's notifications, for main.py (android_message_notification and
 * android_call_notification).
 *
 * A chat's notification lists its unread messages and can be answered or
 * marked read from the shade. An incoming call rings until it is answered,
 * declined or given up on; where Android allows it, it opens the app over
 * the lock screen. What is done to either from the shade comes back through
 * NotifyReceiver, as a small file the app's sync worker acts on.
 *
 * On the lock screen a notification shows only "Ferritext" and a count,
 * unless the phone is set to show private content there.
 */
public final class Notifier {
    public static final String CHANNEL_MESSAGES = "messages";
    public static final String CHANNEL_CALLS = "calls";
    public static final String ACTION_REPLY = "org.fext.notify.REPLY";
    public static final String ACTION_READ = "org.fext.notify.READ";
    public static final String ACTION_DECLINE = "org.fext.notify.DECLINE";
    public static final String KEY_REPLY = "fext_reply";
    private static final long[] RING_VIBRATION = {0, 900, 700, 900, 700};

    private Notifier() {
    }

    private static NotificationManager manager(Context ctx) {
        return (NotificationManager) ctx.getSystemService(
                Context.NOTIFICATION_SERVICE);
    }

    private static int smallIcon(Context ctx) {
        int icon = ctx.getResources().getIdentifier(
                "ic_stat_ferritext", "drawable", ctx.getPackageName());
        return icon != 0 ? icon : ctx.getApplicationInfo().icon;
    }

    private static int immutable() {
        return Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0;
    }

    /** A reply's PendingIntent must let the system add the typed text. */
    private static int mutable() {
        return Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
    }

    private static Notification.Builder builder(Context ctx, String channel) {
        if (Build.VERSION.SDK_INT >= 26) {
            return new Notification.Builder(ctx, channel);
        }
        return new Notification.Builder(ctx);
    }

    /** The two channels: messages, and calls (which ring). */
    public static void ensureChannels(Context ctx) {
        if (Build.VERSION.SDK_INT < 26) {
            return;
        }
        NotificationManager nm = manager(ctx);
        if (nm.getNotificationChannel(CHANNEL_MESSAGES) == null) {
            NotificationChannel messages = new NotificationChannel(
                    CHANNEL_MESSAGES, "Messages",
                    NotificationManager.IMPORTANCE_HIGH);
            messages.setDescription("New messages");
            nm.createNotificationChannel(messages);
        }
        if (nm.getNotificationChannel(CHANNEL_CALLS) == null) {
            NotificationChannel calls = new NotificationChannel(
                    CHANNEL_CALLS, "Calls", NotificationManager.IMPORTANCE_HIGH);
            calls.setDescription("Incoming voice calls");
            calls.setSound(RingtoneManager.getDefaultUri(
                    RingtoneManager.TYPE_RINGTONE),
                    new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                            .setContentType(
                                    AudioAttributes.CONTENT_TYPE_SONIFICATION)
                            .build());
            calls.enableVibration(true);
            calls.setVibrationPattern(RING_VIBRATION);
            nm.createNotificationChannel(calls);
        }
    }

    /** What a tap on the app's icon sends, so the running app resumes. */
    private static Intent launch(Context ctx, String data) {
        Intent intent = ctx.getPackageManager().getLaunchIntentForPackage(
                ctx.getPackageName());
        if (intent == null) {
            intent = new Intent();
        }
        intent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP
                | Intent.FLAG_ACTIVITY_CLEAR_TOP
                | Intent.FLAG_ACTIVITY_NEW_TASK);
        // Distinct data keeps each chat's and each call's intents apart.
        intent.setData(Uri.parse(data));
        return intent;
    }

    private static PendingIntent toReceiver(Context ctx, int code,
                                            String action, String peer, int id,
                                            String me, String call,
                                            boolean typed) {
        Intent intent = new Intent(action, Uri.parse("fext://n/" + code));
        intent.setClass(ctx, NotifyReceiver.class);
        intent.putExtra("peer", peer);
        intent.putExtra("id", id);
        intent.putExtra("me", me);
        if (call != null) {
            intent.putExtra("call", call);
        }
        return PendingIntent.getBroadcast(ctx, code, intent,
                PendingIntent.FLAG_UPDATE_CURRENT
                        | (typed ? mutable() : immutable()));
    }

    private static Notification.Action.Builder action(Context ctx, String title,
                                                      PendingIntent intent) {
        if (Build.VERSION.SDK_INT >= 23) {
            return new Notification.Action.Builder(
                    Icon.createWithResource(ctx, smallIcon(ctx)), title, intent);
        }
        return new Notification.Action.Builder(smallIcon(ctx), title, intent);
    }

    /** What the lock screen shows instead of the words. */
    private static Notification publicVersion(Context ctx, String channel,
                                              String text) {
        return builder(ctx, channel)
                .setSmallIcon(smallIcon(ctx))
                .setContentTitle("Ferritext")
                .setContentText(text)
                .build();
    }

    /**
     * A chat's notification: its unread messages, oldest first (senders,
     * texts and times line up), with Reply (Android 7+) and Mark as read.
     */
    public static void messages(Context ctx, int id, String peer, String title,
                                boolean group, String[] senders, String[] texts,
                                long[] times, boolean canReply, String me,
                                String publicText) {
        int n = Math.min(texts.length, Math.min(senders.length, times.length));
        if (n == 0) {
            return;
        }
        ensureChannels(ctx);
        Notification.Builder b = builder(ctx, CHANNEL_MESSAGES);
        if (Build.VERSION.SDK_INT < 26) {
            b.setPriority(Notification.PRIORITY_HIGH);
            b.setDefaults(Notification.DEFAULT_ALL);
        }
        String last = group ? senders[n - 1] + ": " + texts[n - 1]
                : texts[n - 1];
        b.setSmallIcon(smallIcon(ctx))
                .setContentTitle(title)
                .setContentText(last)
                .setWhen(times[n - 1])
                .setShowWhen(true)
                .setAutoCancel(true)
                .setCategory(Notification.CATEGORY_MESSAGE);
        if (n > 1) {
            b.setNumber(n);
        }
        Intent open = launch(ctx, "fext://chat/" + id);
        open.putExtra("fext_peer", peer);
        b.setContentIntent(PendingIntent.getActivity(ctx, id * 8, open,
                PendingIntent.FLAG_UPDATE_CURRENT | immutable()));

        if (Build.VERSION.SDK_INT >= 28) {
            Person you = new Person.Builder().setName("You").build();
            Notification.MessagingStyle style =
                    new Notification.MessagingStyle(you);
            if (group) {
                style.setConversationTitle(title);
                style.setGroupConversation(true);
            }
            for (int i = 0; i < n; i++) {
                Person who = new Person.Builder().setName(senders[i]).build();
                style.addMessage(new Notification.MessagingStyle.Message(
                        texts[i], times[i], who));
            }
            b.setStyle(style);
        } else if (Build.VERSION.SDK_INT >= 24) {
            Notification.MessagingStyle style =
                    new Notification.MessagingStyle("You");
            if (group) {
                style.setConversationTitle(title);
            }
            for (int i = 0; i < n; i++) {
                style.addMessage(texts[i], times[i], senders[i]);
            }
            b.setStyle(style);
        } else {
            Notification.InboxStyle style = new Notification.InboxStyle();
            for (int i = 0; i < n; i++) {
                style.addLine(group ? senders[i] + ": " + texts[i] : texts[i]);
            }
            b.setStyle(style);
        }

        if (canReply && Build.VERSION.SDK_INT >= 24) {
            RemoteInput input = new RemoteInput.Builder(KEY_REPLY)
                    .setLabel("Reply").build();
            Notification.Action.Builder reply = action(ctx, "Reply",
                    toReceiver(ctx, id * 8 + 1, ACTION_REPLY, peer, id, me,
                            null, true));
            reply.addRemoteInput(input);
            reply.setAllowGeneratedReplies(false);
            if (Build.VERSION.SDK_INT >= 28) {
                reply.setSemanticAction(
                        Notification.Action.SEMANTIC_ACTION_REPLY);
            }
            b.addAction(reply.build());
        }
        Notification.Action.Builder read = action(ctx, "Mark as read",
                toReceiver(ctx, id * 8 + 2, ACTION_READ, peer, id, me, null,
                        false));
        if (Build.VERSION.SDK_INT >= 28) {
            read.setSemanticAction(
                    Notification.Action.SEMANTIC_ACTION_MARK_AS_READ);
        }
        b.addAction(read.build());
        b.setVisibility(Notification.VISIBILITY_PRIVATE);
        b.setPublicVersion(publicVersion(ctx, CHANNEL_MESSAGES, publicText));
        manager(ctx).notify(id, b.build());
    }

    /**
     * An incoming call: rings (the channel's ringtone, repeated) until it is
     * answered, declined or taken away; opens the app over the lock screen
     * where Android allows it. Gives up by itself after a minute.
     */
    public static void incomingCall(Context ctx, int id, String peer,
                                    String callId, String name, String me) {
        ensureChannels(ctx);
        Intent show = launch(ctx, "fext://call/" + callId + "/show");
        show.putExtra("fext_peer", peer);
        show.putExtra("fext_call", callId);
        show.putExtra("fext_call_action", "show");
        PendingIntent full = PendingIntent.getActivity(ctx, id * 8, show,
                PendingIntent.FLAG_UPDATE_CURRENT | immutable());
        Intent answer = launch(ctx, "fext://call/" + callId + "/answer");
        answer.putExtra("fext_peer", peer);
        answer.putExtra("fext_call", callId);
        answer.putExtra("fext_call_action", "answer");
        PendingIntent answerIntent = PendingIntent.getActivity(ctx, id * 8 + 1,
                answer, PendingIntent.FLAG_UPDATE_CURRENT | immutable());
        PendingIntent declineIntent = toReceiver(ctx, id * 8 + 2,
                ACTION_DECLINE, peer, id, me, callId, false);

        Notification.Builder b = builder(ctx, CHANNEL_CALLS);
        if (Build.VERSION.SDK_INT < 26) {
            b.setPriority(Notification.PRIORITY_MAX);
            b.setSound(RingtoneManager.getDefaultUri(
                    RingtoneManager.TYPE_RINGTONE), AudioManager.STREAM_RING);
            b.setVibrate(RING_VIBRATION);
        } else {
            b.setTimeoutAfter(60000);
        }
        b.setSmallIcon(smallIcon(ctx))
                .setContentTitle(name)
                .setContentText("Incoming voice call")
                .setCategory(Notification.CATEGORY_CALL)
                .setOngoing(true)
                .setAutoCancel(false)
                .setContentIntent(full)
                .setFullScreenIntent(full, true)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .setPublicVersion(publicVersion(ctx, CHANNEL_CALLS,
                        "Incoming voice call"));
        if (Build.VERSION.SDK_INT >= 31) {
            Person caller = new Person.Builder().setName(name)
                    .setImportant(true).build();
            b.setStyle(Notification.CallStyle.forIncomingCall(caller,
                    declineIntent, answerIntent));
        } else {
            b.addAction(action(ctx, "Decline", declineIntent).build());
            b.addAction(action(ctx, "Answer", answerIntent).build());
        }
        Notification notification = b.build();
        notification.flags |= Notification.FLAG_INSISTENT;
        manager(ctx).notify(id, notification);
    }

    public static void cancel(Context ctx, int id) {
        manager(ctx).cancel(id);
    }

    /** Whether a call may open the app over the lock screen (Android 14
     * asks the user; earlier versions allow it). */
    public static boolean fullScreenAllowed(Context ctx) {
        if (Build.VERSION.SDK_INT >= 34) {
            return manager(ctx).canUseFullScreenIntent();
        }
        return true;
    }
}
