package org.fext.call;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.AutomaticGainControl;
import android.media.audiofx.NoiseSuppressor;
import android.os.Build;
import android.os.Process;
import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * The sound of a voice call, for main.py (AndroidCallAudio).
 *
 * The microphone is read and the speaker fed on two threads of their own,
 * at audio priority, so speech keeps its pace whatever Python is doing.
 * Python takes every captured frame and hands over frames to play in one
 * call each, as base64 text: pyjnius passes a String across in one piece.
 * Frames are 20 ms of 16 kHz mono; in 16.0's calls 5 ms of 48 kHz stereo.
 * The microphone is read in mono either way (the echo cancelling works on
 * one channel) and, for a stereo call, copied into both channels.
 *
 * The microphone is the platform's voice-call source, which carries the
 * phone's own echo cancelling where it has any; the cancellers are also
 * switched on by hand, as some phones only apply them then. The phone is
 * put in communication mode, so the earpiece is used unless the
 * loudspeaker is chosen.
 *
 * Where the sound goes (11.0): the earpiece, the loudspeaker, a wired
 * headset or a Bluetooth one (routes, setRoute). A call starts on a
 * headset when one is connected, as a phone call does. Android 12+ picks
 * the device itself (setCommunicationDevice); earlier versions open
 * Bluetooth's call link (SCO) for it.
 */
public class CallAudio {
    private final AudioManager audioManager;
    private volatile ArrayBlockingQueue<byte[]> captured =
            new ArrayBlockingQueue<byte[]>(25);
    private volatile ArrayBlockingQueue<byte[]> toPlay =
            new ArrayBlockingQueue<byte[]>(10);
    private volatile boolean running = false;
    /** 16.1: whether the microphone records (a group call's member who
     * has not turned it on hears the others, and records nothing). */
    private volatile boolean capturing = true;
    private AudioRecord record;
    private AudioTrack track;
    private AcousticEchoCanceler echo;
    private NoiseSuppressor noise;
    private AutomaticGainControl gain;
    private Thread micThread;
    private Thread speakerThread;
    private int frameBytes = 640;     // a frame as Python sees it
    private int micBytes = 640;       // a frame from the (mono) microphone
    private int channels = 1;
    private int frameMs = 20;
    /** 18.1: the sound waiting inside the speaker, kept short (tune). */
    private int rateHz = 16000;
    private int depth = 0;            // samples it may hold; 0: all it can
    private int depthMost = 0;
    private int gaps = 0;             // times it ran dry, as last counted
    private int gapsThen = 0;         // ...and a second ago
    private long gapsAt = 0;
    private int previousMode = AudioManager.MODE_NORMAL;
    private String lastError = "";
    private String route = "earpiece";
    private boolean scoStarted = false;
    /** Music and other sound pause for the call, and resume after it. */
    private final AudioManager.OnAudioFocusChangeListener focus =
            new AudioManager.OnAudioFocusChangeListener() {
                public void onAudioFocusChange(int change) {
                    // a call keeps going whatever else wants the speaker
                }
            };

    public CallAudio(Context context) {
        audioManager = (AudioManager) context.getSystemService(
                Context.AUDIO_SERVICE);
    }

    public synchronized boolean start(int rate, int frameSamples,
                                      int channelCount) {
        return start(rate, frameSamples, channelCount, true);
    }

    /** 16.1: with capture false the microphone stays closed until
     * setCapturing(true): a group call's sound starts that way. */
    public synchronized boolean start(int rate, int frameSamples,
                                      int channelCount, boolean capture) {
        if (running) {
            return true;
        }
        capturing = capture;
        rateHz = rate;
        channels = channelCount == 2 ? 2 : 1;
        micBytes = frameSamples * 2;
        frameBytes = micBytes * channels;
        frameMs = Math.max(1, frameSamples * 1000 / rate);
        // Half a second captured, 200 ms to play, whatever a frame's length.
        captured = new ArrayBlockingQueue<byte[]>(Math.max(25, 500 / frameMs));
        toPlay = new ArrayBlockingQueue<byte[]>(Math.max(10, 200 / frameMs));
        int outMask = channels == 2 ? AudioFormat.CHANNEL_OUT_STEREO
                : AudioFormat.CHANNEL_OUT_MONO;
        try {
            int minIn = AudioRecord.getMinBufferSize(rate,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT);
            int minOut = AudioTrack.getMinBufferSize(rate, outMask,
                    AudioFormat.ENCODING_PCM_16BIT);
            if (minIn <= 0 || minOut <= 0) {
                lastError = "this phone cannot record or play " + rate
                        + " Hz sound";
                return false;
            }
            previousMode = audioManager.getMode();
            audioManager.requestAudioFocus(focus,
                    AudioManager.STREAM_VOICE_CALL,
                    AudioManager.AUDIOFOCUS_GAIN_TRANSIENT);
            audioManager.setMode(AudioManager.MODE_IN_COMMUNICATION);
            record = new AudioRecord(
                    MediaRecorder.AudioSource.VOICE_COMMUNICATION, rate,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    Math.max(minIn, micBytes * 4));
            if (record.getState() != AudioRecord.STATE_INITIALIZED) {
                lastError = "the microphone is not available";
                release();
                return false;
            }
            int session = record.getAudioSessionId();
            if (AcousticEchoCanceler.isAvailable()) {
                echo = AcousticEchoCanceler.create(session);
                if (echo != null) {
                    echo.setEnabled(true);
                }
            }
            if (NoiseSuppressor.isAvailable()) {
                noise = NoiseSuppressor.create(session);
                if (noise != null) {
                    noise.setEnabled(true);
                }
            }
            if (AutomaticGainControl.isAvailable()) {
                gain = AutomaticGainControl.create(session);
                if (gain != null) {
                    gain.setEnabled(true);
                }
            }
            AudioAttributes attributes = new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build();
            AudioFormat format = new AudioFormat.Builder()
                    .setSampleRate(rate)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(outMask)
                    .build();
            track = speaker(attributes, format,
                    Math.max(minOut, frameBytes * 3));
            if (track.getState() != AudioTrack.STATE_INITIALIZED) {
                lastError = "the speaker is not available";
                release();
                return false;
            }
            captured.clear();
            toPlay.clear();
            running = true;
            if (capturing) {
                record.startRecording();
            }
            track.play();
            // A headset, if one is connected; else the earpiece.
            String all = routes();
            if (all.contains("bluetooth|")) {
                setRoute("bluetooth");
            } else if (all.contains("wired|")) {
                setRoute("wired");
            }
            micThread = new Thread(new Runnable() {
                public void run() {
                    micLoop();
                }
            }, "fext-call-mic");
            speakerThread = new Thread(new Runnable() {
                public void run() {
                    speakerLoop();
                }
            }, "fext-call-speaker");
            micThread.start();
            speakerThread.start();
            return true;
        } catch (Exception e) {
            lastError = e.toString();
            running = false;
            release();
            return false;
        }
    }

    /** 16.1: open or close the microphone while the sound goes on. Closed,
     * it records nothing, and the phone shows no microphone in use. */
    public synchronized void setCapturing(boolean on) {
        if (!running || record == null || on == capturing) {
            return;
        }
        try {
            if (on) {
                record.startRecording();
            } else {
                record.stop();
                captured.clear();
            }
            capturing = on;
        } catch (IllegalStateException e) {
            lastError = e.toString();
        }
    }

    /** The speaker. 18.1: asked for Android's low-latency path (Android
     * 8; a phone that has none gives its usual one), and holding no more
     * sound than it needs: 40 ms to begin with, where it could hold 80 or
     * more and, written to as fast as it takes, always did. That wait was
     * heard as delay in every call. tune() gives it more when it runs
     * dry. */
    private AudioTrack speaker(AudioAttributes attributes,
                               AudioFormat format, int bytes) {
        AudioTrack made = null;
        if (Build.VERSION.SDK_INT >= 26) {
            try {
                made = new AudioTrack.Builder()
                        .setAudioAttributes(attributes)
                        .setAudioFormat(format)
                        .setBufferSizeInBytes(bytes)
                        .setTransferMode(AudioTrack.MODE_STREAM)
                        .setPerformanceMode(
                                AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
                        .build();
            } catch (Exception refused) {
                made = null;
            }
        }
        if (made == null) {
            made = new AudioTrack(attributes, format, bytes,
                    AudioTrack.MODE_STREAM,
                    AudioManager.AUDIO_SESSION_ID_GENERATE);
        }
        depth = depthMost = gaps = gapsThen = 0;
        gapsAt = System.nanoTime();
        if (Build.VERSION.SDK_INT >= 24
                && made.getState() == AudioTrack.STATE_INITIALIZED) {
            depthMost = made.getBufferCapacityInFrames();
            depth = made.setBufferSizeInFrames(
                    Math.min(depthMost, rateHz * 40 / 1000));
        }
        return made;
    }

    /** After each frame written: a speaker that ran dry twice or more in
     * the last second holds 10 ms more from now on, up to all it can. A
     * phone that needs more than 40 ms finds its own depth in its first
     * seconds; one that does not keeps the delay saved. Once in a second
     * is the app itself held up for a moment, which no depth mends. */
    private void tune() {
        if (Build.VERSION.SDK_INT < 24 || depth <= 0) {
            return;
        }
        gaps = track.getUnderrunCount();
        long at = System.nanoTime();
        if (at - gapsAt < 1000000000L) {
            return;
        }
        if (gaps - gapsThen >= 2 && depth < depthMost) {
            depth = track.setBufferSizeInFrames(
                    Math.min(depthMost, depth + rateHz / 100));
        }
        gapsAt = at;
        gapsThen = gaps;
    }

    /** 18.1, for the app's log: what the speaker holds, in ms, of what it
     * could, how often it ran dry, and whether Android gave its
     * low-latency path. */
    public String facts() {
        try {
            String path = "";
            if (Build.VERSION.SDK_INT >= 26 && track != null) {
                path = track.getPerformanceMode()
                        == AudioTrack.PERFORMANCE_MODE_LOW_LATENCY
                        ? ", low-latency path" : ", usual path";
            }
            return "speaker holds " + depth * 1000 / rateHz + " of "
                    + depthMost * 1000 / rateHz + " ms, ran dry " + gaps
                    + " times" + path;
        } catch (Exception gone) {
            return "";
        }
    }

    private void micLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO);
        byte[] buffer = new byte[micBytes];
        while (running) {
            if (!capturing) {
                try {
                    Thread.sleep(frameMs);
                } catch (InterruptedException e) {
                    return;
                }
                continue;
            }
            int got = 0;
            while (got < micBytes && running) {
                int n = record.read(buffer, got, micBytes - got);
                if (n <= 0) {
                    break;
                }
                got += n;
            }
            if (got < micBytes) {
                if (running) {
                    try {
                        Thread.sleep(5);
                    } catch (InterruptedException e) {
                        return;
                    }
                }
                continue;
            }
            byte[] frame = channels == 1 ? buffer.clone() : stereo(buffer);
            if (!captured.offer(frame)) {
                captured.poll();            // Python fell behind: drop the oldest
                captured.offer(frame);
            }
        }
    }

    /** A mono frame in both channels of a stereo one. */
    private static byte[] stereo(byte[] mono) {
        byte[] out = new byte[mono.length * 2];
        for (int i = 0, j = 0; i < mono.length; i += 2, j += 4) {
            out[j] = mono[i];
            out[j + 1] = mono[i + 1];
            out[j + 2] = mono[i];
            out[j + 3] = mono[i + 1];
        }
        return out;
    }

    private void speakerLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO);
        byte[] silence = new byte[frameBytes];
        while (running) {
            byte[] frame;
            try {
                frame = toPlay.poll(frameMs + 10, TimeUnit.MILLISECONDS);
            } catch (InterruptedException e) {
                return;
            }
            // Nothing in time: a frame of silence keeps the speaker running
            // without a click.
            track.write(frame != null ? frame : silence, 0, frameBytes);
            tune();
        }
    }

    /** Every captured frame, one after another, as base64; null if none
     * is waiting. */
    public String readFrames() {
        byte[] frame = captured.poll();
        if (frame == null) {
            return null;
        }
        ByteArrayOutputStream all = new ByteArrayOutputStream(frame.length * 4);
        while (frame != null) {
            all.write(frame, 0, frame.length);
            frame = captured.poll();
        }
        return Base64.encodeToString(all.toByteArray(), Base64.NO_WRAP);
    }

    /** Whole frames to play, one after another, as base64. */
    public void writeFrames(String b64) {
        byte[] all = Base64.decode(b64, Base64.NO_WRAP);
        for (int at = 0; at + frameBytes <= all.length; at += frameBytes) {
            byte[] frame = Arrays.copyOfRange(all, at, at + frameBytes);
            if (!toPlay.offer(frame)) {
                toPlay.poll();             // too far behind: drop the oldest
                toPlay.offer(frame);
            }
        }
    }

    /** Frames waiting to be played. */
    public int queued() {
        return toPlay.size();
    }

    public void setSpeaker(boolean on) {
        setRoute(on ? "speaker" : "earpiece");
    }

    /** The ways the call's sound can go now: "key|label" pieces joined
     * by ";", of earpiece, speaker, wired and bluetooth. */
    public synchronized String routes() {
        List<String> out = new ArrayList<String>();
        boolean wired = false;
        String bluetooth = null;
        if (Build.VERSION.SDK_INT >= 23) {
            for (AudioDeviceInfo d : outputs()) {
                int t = d.getType();
                if (t == AudioDeviceInfo.TYPE_WIRED_HEADSET
                        || t == AudioDeviceInfo.TYPE_WIRED_HEADPHONES
                        || t == AudioDeviceInfo.TYPE_USB_HEADSET) {
                    wired = true;
                } else if (isBluetooth(t) && bluetooth == null) {
                    CharSequence name = d.getProductName();
                    bluetooth = name == null || name.length() == 0
                            ? "Bluetooth" : name.toString();
                }
            }
        } else {
            wired = audioManager.isWiredHeadsetOn();
            if (audioManager.isBluetoothScoAvailableOffCall()
                    && audioManager.isBluetoothA2dpOn()) {
                bluetooth = "Bluetooth";
            }
        }
        out.add(wired ? "wired|Headset" : "earpiece|Phone");
        out.add("speaker|Speaker");
        if (bluetooth != null) {
            out.add("bluetooth|" + bluetooth.replace("|", " ")
                    .replace(";", " "));
        }
        StringBuilder joined = new StringBuilder();
        for (String piece : out) {
            if (joined.length() > 0) {
                joined.append(';');
            }
            joined.append(piece);
        }
        return joined.toString();
    }

    public synchronized String route() {
        return route;
    }

    /** Send the call's sound by `key`: earpiece, speaker, wired or
     * bluetooth. False if there is no such way now. */
    public synchronized boolean setRoute(String key) {
        try {
            if (Build.VERSION.SDK_INT >= 31) {
                AudioDeviceInfo want = null;
                for (AudioDeviceInfo d
                        : audioManager.getAvailableCommunicationDevices()) {
                    if (key.equals(keyOf(d.getType()))) {
                        want = d;
                        break;
                    }
                }
                if (want == null || !audioManager.setCommunicationDevice(want)) {
                    return false;
                }
            } else {
                if ("bluetooth".equals(key)) {
                    audioManager.setSpeakerphoneOn(false);
                    audioManager.startBluetoothSco();
                    audioManager.setBluetoothScoOn(true);
                    scoStarted = true;
                } else {
                    stopSco();
                    audioManager.setSpeakerphoneOn("speaker".equals(key));
                }
            }
            route = key;
            return true;
        } catch (Exception e) {
            lastError = e.toString();
            return false;
        }
    }

    private AudioDeviceInfo[] outputs() {
        if (Build.VERSION.SDK_INT >= 31) {
            List<AudioDeviceInfo> list =
                    audioManager.getAvailableCommunicationDevices();
            return list.toArray(new AudioDeviceInfo[0]);
        }
        return audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS);
    }

    private static boolean isBluetooth(int type) {
        return type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
                || (Build.VERSION.SDK_INT >= 31
                    && type == AudioDeviceInfo.TYPE_BLE_HEADSET);
    }

    private static String keyOf(int type) {
        if (type == AudioDeviceInfo.TYPE_BUILTIN_EARPIECE) {
            return "earpiece";
        }
        if (type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER) {
            return "speaker";
        }
        if (type == AudioDeviceInfo.TYPE_WIRED_HEADSET
                || type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES
                || type == AudioDeviceInfo.TYPE_USB_HEADSET) {
            return "wired";
        }
        if (isBluetooth(type)) {
            return "bluetooth";
        }
        return "";
    }

    private void stopSco() {
        if (scoStarted) {
            audioManager.setBluetoothScoOn(false);
            audioManager.stopBluetoothSco();
            scoStarted = false;
        }
    }

    public String lastError() {
        return lastError;
    }

    public synchronized void stop() {
        running = false;
        for (Thread thread : new Thread[] {micThread, speakerThread}) {
            if (thread != null) {
                try {
                    thread.join(500);
                } catch (InterruptedException e) {
                    break;
                }
            }
        }
        micThread = null;
        speakerThread = null;
        release();
    }

    private void release() {
        if (record != null) {
            try {
                record.stop();
            } catch (Exception e) {
                // not recording
            }
            record.release();
            record = null;
        }
        if (track != null) {
            try {
                track.stop();
            } catch (Exception e) {
                // not playing
            }
            track.release();
            track = null;
        }
        if (echo != null) {
            echo.release();
            echo = null;
        }
        if (noise != null) {
            noise.release();
            noise = null;
        }
        if (gain != null) {
            gain.release();
            gain = null;
        }
        try {
            if (Build.VERSION.SDK_INT >= 31) {
                audioManager.clearCommunicationDevice();
            }
            stopSco();
            audioManager.setSpeakerphoneOn(false);
            audioManager.setMode(previousMode);
            audioManager.abandonAudioFocus(focus);
        } catch (Exception e) {
            // the phone keeps its mode
        }
        route = "earpiece";
    }
}
