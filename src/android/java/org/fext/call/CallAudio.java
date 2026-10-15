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

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * The sound of a voice call, for main.py (AndroidCallAudio).
 *
 * The microphone is read and the speaker fed on two threads of their own,
 * at audio priority, so speech keeps its pace whatever Python is doing.
 * Python takes captured frames and hands over frames to play, 20 ms at a
 * time, as base64 text: pyjnius passes a String across in one piece.
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
    private final ArrayBlockingQueue<byte[]> captured =
            new ArrayBlockingQueue<byte[]>(25);
    private final ArrayBlockingQueue<byte[]> toPlay =
            new ArrayBlockingQueue<byte[]>(10);
    private volatile boolean running = false;
    private AudioRecord record;
    private AudioTrack track;
    private AcousticEchoCanceler echo;
    private NoiseSuppressor noise;
    private AutomaticGainControl gain;
    private Thread micThread;
    private Thread speakerThread;
    private int frameBytes = 640;
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

    public synchronized boolean start(int rate, int frameSamples) {
        if (running) {
            return true;
        }
        frameBytes = frameSamples * 2;
        try {
            int minIn = AudioRecord.getMinBufferSize(rate,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT);
            int minOut = AudioTrack.getMinBufferSize(rate,
                    AudioFormat.CHANNEL_OUT_MONO,
                    AudioFormat.ENCODING_PCM_16BIT);
            if (minIn <= 0 || minOut <= 0) {
                lastError = "this phone cannot record or play 16 kHz sound";
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
                    Math.max(minIn, frameBytes * 4));
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
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build();
            track = new AudioTrack(attributes, format,
                    Math.max(minOut, frameBytes * 3), AudioTrack.MODE_STREAM,
                    AudioManager.AUDIO_SESSION_ID_GENERATE);
            if (track.getState() != AudioTrack.STATE_INITIALIZED) {
                lastError = "the speaker is not available";
                release();
                return false;
            }
            captured.clear();
            toPlay.clear();
            running = true;
            record.startRecording();
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

    private void micLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO);
        byte[] buffer = new byte[frameBytes];
        while (running) {
            int got = 0;
            while (got < frameBytes && running) {
                int n = record.read(buffer, got, frameBytes - got);
                if (n <= 0) {
                    break;
                }
                got += n;
            }
            if (got < frameBytes) {
                if (running) {
                    try {
                        Thread.sleep(5);
                    } catch (InterruptedException e) {
                        return;
                    }
                }
                continue;
            }
            byte[] frame = buffer.clone();
            if (!captured.offer(frame)) {
                captured.poll();            // Python fell behind: drop the oldest
                captured.offer(frame);
            }
        }
    }

    private void speakerLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO);
        byte[] silence = new byte[frameBytes];
        while (running) {
            byte[] frame;
            try {
                frame = toPlay.poll(30, TimeUnit.MILLISECONDS);
            } catch (InterruptedException e) {
                return;
            }
            // Nothing in time: a frame of silence keeps the speaker running
            // without a click.
            track.write(frame != null ? frame : silence, 0, frameBytes);
        }
    }

    /** One captured frame as base64, or null if none is waiting. */
    public String readFrame() {
        byte[] frame = captured.poll();
        return frame == null ? null
                : Base64.encodeToString(frame, Base64.NO_WRAP);
    }

    /** A frame to play, as base64. */
    public void writeFrame(String b64) {
        byte[] frame = Base64.decode(b64, Base64.NO_WRAP);
        if (frame.length != frameBytes) {
            return;
        }
        if (!toPlay.offer(frame)) {
            toPlay.poll();
            toPlay.offer(frame);
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
