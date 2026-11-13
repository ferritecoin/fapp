package org.fext.call;

import android.app.Activity;
import android.content.Context;
import android.graphics.SurfaceTexture;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.Image;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaFormat;
import android.opengl.EGL14;
import android.opengl.EGLConfig;
import android.opengl.EGLContext;
import android.opengl.EGLDisplay;
import android.opengl.EGLExt;
import android.opengl.EGLSurface;
import android.opengl.GLES11Ext;
import android.opengl.GLES20;
import android.opengl.Matrix;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.util.Base64;
import android.util.Range;
import android.util.Size;
import android.view.OrientationEventListener;
import android.view.Surface;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * A call's pictures on a phone (17.0), for main.py (AndroidVideoCoder,
 * AndroidVideoDecoder).
 *
 * The camera (Camera2) draws into one OpenGL texture; on a thread of its
 * own each picture is drawn from it, upright, into a hardware encoder's
 * input for every size a call wants (setPlan: "layer:short:fps:kbps;..."),
 * and now and then into a small picture for this phone's own tile
 * (preview). Coded pictures wait in a queue for Python (poll), each as
 * "layer:codec:key:base64", a key frame with its codec's settings (SPS)
 * in front, so a receiver can start from it. Pictures cross as base64
 * text, as the call's sound does (CallAudio): pyjnius passes a String in
 * one piece.
 *
 * A Decoder turns another phone's or computer's coded pictures back into
 * RGBA rows, scaled to the tile that shows them, for a Kivy texture.
 *
 * Which way up (18.1). Android hands the camera's pictures over with a
 * matrix (SurfaceTexture.getTransformMatrix) that already turns them
 * upright for the phone's natural way up, and mirrors the front camera's
 * as a mirror would. So a picture is turned here only by what is left:
 * for the one sent, by how the phone is held (its orientation sensor: the
 * person seen stays upright when the phone is turned on its side), the
 * front camera's mirroring taken out; for this phone's own tile, by how
 * the app's own page is turned, the mirroring kept. Before 18.1 the
 * camera's own turn was applied a second time: a phone held upright sent
 * a picture on its side, squeezed into an upright frame.
 *
 * H.264 always; HEVC and AV1 only where this phone codes or decodes them
 * in hardware (codecs). Everything here waits for a phone to prove it.
 */
public class VideoCall {
    private static final String[][] MIMES = {
            {"av1", "video/av01"}, {"hevc", "video/hevc"},
            {"h264", "video/avc"}};
    private static final int EGL_RECORDABLE_ANDROID = 0x3142;

    private final Context context;
    private final HandlerThread thread;
    private final Handler handler;
    private final LinkedBlockingQueue<String> coded =
            new LinkedBlockingQueue<String>(120);
    private volatile String lastError = "";
    /** 17.3: a fault on this class's own thread ("fext-video") stopped
     * the video (see fail). */
    private volatile boolean failed = false;
    private volatile String previewOut = "";
    private volatile int previewW = 0;
    private volatile int previewH = 0;
    private boolean front = true;
    private int screen = 0;           // the display's turn, in degrees
    /** The camera's SENSOR_ORIENTATION: 90 or 270 on a phone, whose
     * camera lies on its side; Android's matrix has undone it. */
    private int sensor = 0;
    /** 18.1: how the phone is held, in degrees clockwise from its natural
     * way up (0, 90, 180, 270), as its orientation sensor says. */
    private int held = 0;
    private int heldSeen = 0;         // the same, on the sensor's thread
    private volatile boolean following = true;
    private OrientationEventListener holding;
    private String plan = "";         // what setPlan asked for last
    private String planCodec = "";
    private Size captureSize = new Size(1280, 720);
    private CameraDevice camera;
    private CameraCaptureSession session;
    private SurfaceTexture texture;
    private Surface cameraSurface;
    private final List<Layer> layers = new ArrayList<Layer>();
    private long previewAt = 0;
    private EGLDisplay display = EGL14.EGL_NO_DISPLAY;
    private EGLContext eglContext = EGL14.EGL_NO_CONTEXT;
    private EGLConfig eglConfig;
    private EGLSurface pbuffer = EGL14.EGL_NO_SURFACE;
    private int program, oesTexture, aPos, aTex, uPos, uTex;
    private int fbo, fboTexture, fboW, fboH;
    private final float[] texMatrix = new float[16];
    private static final FloatBuffer QUAD = floats(
            new float[]{-1f, -1f, 1f, -1f, -1f, 1f, 1f, 1f});
    private static final FloatBuffer CORNERS = floats(
            new float[]{0f, 0f, 1f, 0f, 0f, 1f, 1f, 1f});

    /** One size of the picture, coded. */
    private static class Layer {
        int index, shortSide, fps, kbps;
        String codec;
        MediaCodec encoder;
        EGLSurface surface = EGL14.EGL_NO_SURFACE;
        byte[] settings = new byte[0];
        long nextAt = 0;
        int width, height;
    }

    // ---- what this phone can code ------------------------------------------
    /** The codecs this phone's coders take, best first, comma separated:
     * HEVC and AV1 only from a coder in hardware (a software one is too
     * slow for a call), and H.264 always: Android's own software coders
     * take it on every phone (17.1: a list that would not say, or a
     * vendor's coder that threw when asked, cost a phone its video). */
    public static String codecs(boolean encoders) {
        StringBuilder out = new StringBuilder();
        MediaCodecInfo[] infos;
        try {
            infos = new MediaCodecList(MediaCodecList.REGULAR_CODECS)
                    .getCodecInfos();
        } catch (RuntimeException error) {
            return "h264";
        }
        for (String[] pair : MIMES) {
            if (pair[0].equals("h264")) {
                continue;
            }
            for (MediaCodecInfo info : infos) {
                if (offers(info, encoders, pair[1])) {
                    out.append(pair[0]).append(',');
                    break;
                }
            }
        }
        return out.append("h264").toString();
    }

    /** Whether `info` codes `mime` in hardware, the way asked; a coder
     * that throws on being asked does not. */
    private static boolean offers(MediaCodecInfo info, boolean encoders,
                                  String mime) {
        try {
            return info.isEncoder() == encoders && takes(info, mime)
                    && hardware(info);
        } catch (RuntimeException error) {
            return false;
        }
    }

    private static boolean takes(MediaCodecInfo info, String mime) {
        for (String type : info.getSupportedTypes()) {
            if (type.equalsIgnoreCase(mime)) {
                return true;
            }
        }
        return false;
    }

    private static boolean hardware(MediaCodecInfo info) {
        if (Build.VERSION.SDK_INT >= 29) {
            return info.isHardwareAccelerated();
        }
        String name = info.getName().toLowerCase(Locale.ROOT);
        return !(name.startsWith("omx.google.")
                || name.startsWith("c2.android.") || name.contains(".sw."));
    }

    static String mimeOf(String codec) {
        for (String[] pair : MIMES) {
            if (pair[0].equals(codec)) {
                return pair[1];
            }
        }
        return "video/avc";
    }

    // ---- the camera ---------------------------------------------------------------
    public VideoCall(Activity activity) {
        context = activity.getApplicationContext();
        int turned = 0;
        try {
            switch (activity.getWindowManager().getDefaultDisplay()
                    .getRotation()) {
                case Surface.ROTATION_90: turned = 90; break;
                case Surface.ROTATION_180: turned = 180; break;
                case Surface.ROTATION_270: turned = 270; break;
                default: turned = 0;
            }
        } catch (Exception error) {
            turned = 0;
        }
        screen = turned;
        thread = new HandlerThread("fext-video");
        thread.start();
        handler = new Handler(thread.getLooper());
        handler.post(new Runnable() {
            @Override
            public void run() {
                try {
                    setUpGl();
                    openCamera();
                } catch (Exception error) {
                    lastError = String.valueOf(error);
                }
            }
        });
        try {
            watchHolding();
        } catch (Exception error) {
            holding = null;              // no sensor: as held upright
        }
    }

    /** 18.1: the phone's orientation sensor, watched while the camera is
     * on. A quarter turn counts once the phone is 60 degrees from where
     * it was, so a hand that wavers at the diagonal turns nothing. */
    private void watchHolding() {
        holding = new OrientationEventListener(context) {
            @Override
            public void onOrientationChanged(int degrees) {
                if (degrees == ORIENTATION_UNKNOWN || !following) {
                    return;                          // lying flat, or kept
                }
                int off = Math.abs(degrees - heldSeen);
                if (Math.min(off, 360 - off) <= 60) {
                    return;
                }
                final int now = ((degrees + 45) / 90 * 90) % 360;
                heldSeen = now;
                handler.post(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            hold(now);
                        } catch (RuntimeException error) {
                            fail("the video could not be turned", error);
                        }
                    }
                });
            }
        };
        if (holding.canDetectOrientation()) {
            holding.enable();
        }
    }

    /** Whether the picture sent goes on following how the phone is
     * held. A call's does; a video message keeps the way it began, since
     * a file's pictures are all one shape (main.VideoNoteRecorder). */
    public void follow(boolean on) {
        following = on;
    }

    /** The phone is now held `degrees` from its natural way up: the
     * picture sent is turned to stay upright, and where that changes its
     * shape (upright to lying), its coders are made again. */
    private void hold(int degrees) {
        boolean was = lying(sentTurn());
        held = degrees;
        if (was != lying(sentTurn())) {
            replan();
        } else {
            for (Layer layer : layers) {
                askKeyNow(layer);
            }
        }
    }

    /** How far the picture sent is turned, clockwise, after Android's
     * matrix: as the phone is held; the other way for the front camera,
     * which looks back at the person holding it. */
    private int sentTurn() {
        return front ? (360 - held) % 360 : held;
    }

    /** How far this phone's own tile is turned: against the turn of the
     * app's own page (none on a phone, whose page is upright). */
    private int shownTurn() {
        return (360 - screen) % 360;
    }

    /** Whether a picture turned by `turn` is the capture's shape turned
     * on its side (a phone held upright: 1280x720 becomes 720x1280). */
    private boolean lying(int turn) {
        return (sensor + turn) % 180 == 90;
    }

    /** 18.1, for the emulators' check: the turns in use and what is
     * coded, in words. */
    public String facts() {
        StringBuilder out = new StringBuilder();
        out.append(front ? "front" : "back").append(" camera, sensor ")
                .append(sensor).append(", held ").append(held)
                .append(", page ").append(screen).append(", from ")
                .append(captureSize.getWidth()).append("x")
                .append(captureSize.getHeight()).append(":");
        try {
            for (Layer layer : layers) {
                out.append(" ").append(layer.width).append("x")
                        .append(layer.height);
            }
        } catch (RuntimeException changing) {
            out.append(" (changing)");   // the video's thread, mid-plan
        }
        return out.toString();
    }

    public String error() {
        return lastError;
    }

    /** 17.3: a fault on the video's own thread ends the video, not the
     * app. An exception there is not Python's to catch: it closed the app
     * (Android 5's coder threw IllegalStateException from
     * dequeueOutputBuffer; the Devices run on API 21, 17.2.1). It is kept
     * as the error, which Python reads, stopping the camera and saying
     * why; nothing more is drawn meanwhile. The call goes on. */
    private void fail(String what, RuntimeException error) {
        failed = true;
        lastError = what + ": " + error;
    }

    private void openCamera() throws Exception {
        CameraManager manager = (CameraManager) context.getSystemService(
                Context.CAMERA_SERVICE);
        String chosen = null;
        CameraCharacteristics traits = null;
        for (String id : manager.getCameraIdList()) {
            CameraCharacteristics c = manager.getCameraCharacteristics(id);
            Integer facing = c.get(CameraCharacteristics.LENS_FACING);
            boolean isFront = facing != null
                    && facing == CameraCharacteristics.LENS_FACING_FRONT;
            if (chosen == null || isFront == front) {
                chosen = id;
                traits = c;
                if (isFront == front) {
                    break;
                }
            }
        }
        if (chosen == null) {
            lastError = "this phone has no camera";
            return;
        }
        Integer turned = traits.get(
                CameraCharacteristics.SENSOR_ORIENTATION);
        sensor = turned == null ? 0 : turned;
        StreamConfigurationMap map = traits.get(
                CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
        captureSize = pickSize(map == null ? null
                : map.getOutputSizes(SurfaceTexture.class));
        Range<Integer> fps = pickFps(traits.get(
                CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES));
        texture.setDefaultBufferSize(captureSize.getWidth(),
                captureSize.getHeight());
        final Range<Integer> rate = fps;
        manager.openCamera(chosen, new CameraDevice.StateCallback() {
            @Override
            public void onOpened(CameraDevice device) {
                camera = device;
                startSession(rate);
            }

            @Override
            public void onDisconnected(CameraDevice device) {
                device.close();
                camera = null;
            }

            @Override
            public void onError(CameraDevice device, int code) {
                device.close();
                camera = null;
                lastError = "the camera failed (" + code + ")";
            }
        }, handler);
    }

    private void startSession(final Range<Integer> rate) {
        try {
            camera.createCaptureSession(Arrays.asList(cameraSurface),
                    new CameraCaptureSession.StateCallback() {
                        @Override
                        public void onConfigured(CameraCaptureSession s) {
                            session = s;
                            try {
                                CaptureRequest.Builder request =
                                        camera.createCaptureRequest(
                                                CameraDevice.TEMPLATE_RECORD);
                                request.addTarget(cameraSurface);
                                if (rate != null) {
                                    request.set(CaptureRequest
                                            .CONTROL_AE_TARGET_FPS_RANGE,
                                            rate);
                                }
                                s.setRepeatingRequest(request.build(), null,
                                        handler);
                            } catch (Exception error) {
                                lastError = String.valueOf(error);
                            }
                        }

                        @Override
                        public void onConfigureFailed(
                                CameraCaptureSession s) {
                            lastError = "the camera would not start";
                        }
                    }, handler);
        } catch (Exception error) {
            lastError = String.valueOf(error);
        }
    }

    /** A 16:9 size near 1280 × 720 (4:3 when none), as the sensor gives
     * it (wider than tall). */
    private static Size pickSize(Size[] sizes) {
        Size best = new Size(1280, 720);
        if (sizes == null) {
            return best;
        }
        int bestScore = Integer.MAX_VALUE;
        for (Size size : sizes) {
            int w = size.getWidth(), h = size.getHeight();
            boolean wide = w * 9 == h * 16;
            int score = Math.abs(w - 1280) + Math.abs(h - 720)
                    + (wide ? 0 : 400) + (w > 1920 ? 4000 : 0);
            if (score < bestScore) {
                bestScore = score;
                best = size;
            }
        }
        return best;
    }

    /** The steadiest rate up to 30 frames a second. */
    private static Range<Integer> pickFps(Range<Integer>[] ranges) {
        Range<Integer> best = null;
        if (ranges == null) {
            return null;
        }
        for (Range<Integer> range : ranges) {
            if (range.getUpper() > 30) {
                continue;
            }
            if (best == null || range.getUpper() > best.getUpper()
                    || (range.getUpper().equals(best.getUpper())
                    && range.getLower() > best.getLower())) {
                best = range;
            }
        }
        return best;
    }

    /** The other camera (front, back). */
    public void flip() {
        handler.post(new Runnable() {
            @Override
            public void run() {
                boolean was = lying(sentTurn());
                front = !front;
                closeCamera();
                try {
                    openCamera();
                    if (was != lying(sentTurn())) {
                        replan();
                    }
                } catch (Exception error) {
                    lastError = String.valueOf(error);
                }
                for (Layer layer : layers) {
                    askKeyNow(layer);
                }
            }
        });
    }

    private void closeCamera() {
        if (session != null) {
            try {
                session.close();
            } catch (Exception ignored) {
                // closing anyway
            }
            session = null;
        }
        if (camera != null) {
            camera.close();
            camera = null;
        }
    }

    // ---- what a call wants: the sizes, and the codec ---------------------------
    /** "layer:short:fps:kbps;..." in `codec`; "" codes nothing. A phone
     * codes at most two sizes: the largest and the smallest asked for. */
    public void setPlan(final String plan, final String codec) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                try {
                    applyPlan(plan, codec);
                } catch (Exception error) {
                    lastError = "the plan " + plan + ": " + error;
                }
            }
        });
    }

    /** Every size's coder made again, for a picture whose shape changed
     * (hold, flip). */
    private void replan() {
        for (Layer layer : layers) {
            release(layer);
        }
        layers.clear();
        applyPlan(plan, planCodec);
    }

    private void applyPlan(String plan, String codec) {
        this.plan = plan;
        planCodec = codec;
        List<int[]> wanted = new ArrayList<int[]>();
        for (String part : plan.split(";")) {
            String[] f = part.split(":");
            if (f.length == 4) {
                wanted.add(new int[]{Integer.parseInt(f[0]),
                        Integer.parseInt(f[1]), Integer.parseInt(f[2]),
                        Integer.parseInt(f[3])});
            }
        }
        while (wanted.size() > 2) {
            wanted.remove(1);                    // the middle one goes
        }
        List<Layer> keep = new ArrayList<Layer>();
        for (int[] w : wanted) {
            Layer found = null;
            for (Layer layer : layers) {
                if (layer.index == w[0] && layer.shortSide == w[1]
                        && layer.fps == w[2] && layer.codec.equals(codec)) {
                    found = layer;
                }
            }
            if (found != null) {
                if (found.kbps != w[3]) {
                    found.kbps = w[3];
                    Bundle change = new Bundle();
                    change.putInt(MediaCodec.PARAMETER_KEY_VIDEO_BITRATE,
                            w[3] * 1000);
                    found.encoder.setParameters(change);
                }
                keep.add(found);
                continue;
            }
            Layer made = makeLayer(w[0], w[1], w[2], w[3], codec);
            if (made != null) {
                keep.add(made);
            }
        }
        for (Layer layer : layers) {
            if (!keep.contains(layer)) {
                release(layer);
            }
        }
        layers.clear();
        layers.addAll(keep);
    }

    private Layer makeLayer(int index, int shortSide, int fps, int kbps,
                            String codec) {
        Layer layer = new Layer();
        layer.index = index;
        layer.shortSide = shortSide;
        layer.fps = fps;
        layer.kbps = kbps;
        layer.codec = codec;
        int w = captureSize.getWidth(), h = captureSize.getHeight();
        double k = Math.min(1.0, shortSide / (double) Math.min(w, h));
        int lw = ((int) (w * k)) & ~15, lh = ((int) (h * k)) & ~15;
        boolean upright = lying(sentTurn());
        layer.width = upright ? lh : lw;
        layer.height = upright ? lw : lh;
        try {
            String mime = mimeOf(codec);
            MediaFormat format = MediaFormat.createVideoFormat(mime,
                    layer.width, layer.height);
            format.setInteger(MediaFormat.KEY_COLOR_FORMAT,
                    MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface);
            format.setInteger(MediaFormat.KEY_BIT_RATE, kbps * 1000);
            format.setInteger(MediaFormat.KEY_FRAME_RATE, fps);
            format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 10);
            if (Build.VERSION.SDK_INT >= 23) {
                format.setInteger(MediaFormat.KEY_PRIORITY, 0);  // real time
            }
            layer.encoder = MediaCodec.createEncoderByType(mime);
            layer.encoder.configure(format, null, null,
                    MediaCodec.CONFIGURE_FLAG_ENCODE);
            Surface input = layer.encoder.createInputSurface();
            layer.encoder.start();
            layer.surface = EGL14.eglCreateWindowSurface(display, eglConfig,
                    input, new int[]{EGL14.EGL_NONE}, 0);
            return layer;
        } catch (Exception error) {
            lastError = "no " + codec + " coder for " + layer.width + "x"
                    + layer.height + ": " + error;
            release(layer);
            return null;
        }
    }

    private void release(Layer layer) {
        if (layer.surface != EGL14.EGL_NO_SURFACE) {
            EGL14.eglDestroySurface(display, layer.surface);
            layer.surface = EGL14.EGL_NO_SURFACE;
        }
        if (layer.encoder != null) {
            try {
                layer.encoder.stop();
            } catch (Exception ignored) {
                // released below either way
            }
            layer.encoder.release();
            layer.encoder = null;
        }
    }

    /** A key frame of one size (-1: of every size). */
    public void askKey(final int index) {
        handler.post(new Runnable() {
            @Override
            public void run() {
                try {
                    for (Layer layer : layers) {
                        if (index < 0 || layer.index == index) {
                            askKeyNow(layer);
                        }
                    }
                } catch (RuntimeException error) {
                    fail("a key frame could not be asked for", error);
                }
            }
        });
    }

    private static void askKeyNow(Layer layer) {
        if (layer.encoder != null) {
            Bundle ask = new Bundle();
            ask.putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0);
            layer.encoder.setParameters(ask);
        }
    }

    // ---- each picture, drawn where it is wanted --------------------------------
    private void setUpGl() {
        display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY);
        int[] version = new int[2];
        EGL14.eglInitialize(display, version, 0, version, 1);
        int[] wanted = {EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8,
                EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_ALPHA_SIZE, 8,
                EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_OPENGL_ES2_BIT,
                EGL_RECORDABLE_ANDROID, 1, EGL14.EGL_NONE};
        EGLConfig[] configs = new EGLConfig[1];
        int[] count = new int[1];
        EGL14.eglChooseConfig(display, wanted, 0, configs, 0, 1, count, 0);
        eglConfig = configs[0];
        eglContext = EGL14.eglCreateContext(display, eglConfig,
                EGL14.EGL_NO_CONTEXT,
                new int[]{EGL14.EGL_CONTEXT_CLIENT_VERSION, 2,
                        EGL14.EGL_NONE}, 0);
        pbuffer = EGL14.eglCreatePbufferSurface(display, eglConfig,
                new int[]{EGL14.EGL_WIDTH, 1, EGL14.EGL_HEIGHT, 1,
                        EGL14.EGL_NONE}, 0);
        EGL14.eglMakeCurrent(display, pbuffer, pbuffer, eglContext);
        program = link(
                "uniform mat4 uPos;\nuniform mat4 uTex;\n"
                        + "attribute vec4 aPos;\nattribute vec4 aTex;\n"
                        + "varying vec2 vTex;\nvoid main() {\n"
                        + "  gl_Position = uPos * aPos;\n"
                        + "  vTex = (uTex * aTex).xy;\n}\n",
                "#extension GL_OES_EGL_image_external : require\n"
                        + "precision mediump float;\nvarying vec2 vTex;\n"
                        + "uniform samplerExternalOES sTex;\n"
                        + "void main() {\n"
                        + "  gl_FragColor = texture2D(sTex, vTex);\n}\n");
        aPos = GLES20.glGetAttribLocation(program, "aPos");
        aTex = GLES20.glGetAttribLocation(program, "aTex");
        uPos = GLES20.glGetUniformLocation(program, "uPos");
        uTex = GLES20.glGetUniformLocation(program, "uTex");
        int[] ids = new int[1];
        GLES20.glGenTextures(1, ids, 0);
        oesTexture = ids[0];
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, oesTexture);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,
                GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,
                GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
        texture = new SurfaceTexture(oesTexture);
        texture.setOnFrameAvailableListener(
                new SurfaceTexture.OnFrameAvailableListener() {
                    @Override
                    public void onFrameAvailable(SurfaceTexture t) {
                        try {
                            onPicture();
                        } catch (RuntimeException error) {
                            fail("the video coder failed", error);
                        }
                    }
                }, handler);
        cameraSurface = new Surface(texture);
    }

    private static int link(String vertex, String fragment) {
        int program = GLES20.glCreateProgram();
        GLES20.glAttachShader(program, shader(GLES20.GL_VERTEX_SHADER,
                vertex));
        GLES20.glAttachShader(program, shader(GLES20.GL_FRAGMENT_SHADER,
                fragment));
        GLES20.glLinkProgram(program);
        return program;
    }

    private static int shader(int kind, String source) {
        int shader = GLES20.glCreateShader(kind);
        GLES20.glShaderSource(shader, source);
        GLES20.glCompileShader(shader);
        return shader;
    }

    private static FloatBuffer floats(float[] values) {
        FloatBuffer buffer = ByteBuffer.allocateDirect(values.length * 4)
                .order(ByteOrder.nativeOrder()).asFloatBuffer();
        buffer.put(values).position(0);
        return buffer;
    }

    /** A new picture from the camera: drawn, upright, into each size due,
     * and the small one now and then; what the coders made, queued. */
    private void onPicture() {
        if (failed || display == EGL14.EGL_NO_DISPLAY) {
            return;
        }
        EGL14.eglMakeCurrent(display, pbuffer, pbuffer, eglContext);
        texture.updateTexImage();
        texture.getTransformMatrix(texMatrix);
        long now = System.nanoTime();
        for (Layer layer : layers) {
            if (layer.surface == EGL14.EGL_NO_SURFACE
                    || now < layer.nextAt - 4000000L) {
                continue;
            }
            layer.nextAt = Math.max(layer.nextAt + 1000000000L / layer.fps,
                    now - 500000000L / layer.fps);
            EGL14.eglMakeCurrent(display, layer.surface, layer.surface,
                    eglContext);
            GLES20.glViewport(0, 0, layer.width, layer.height);
            draw(sentTurn(), front);
            EGLExt.eglPresentationTimeANDROID(display, layer.surface,
                    texture.getTimestamp());
            EGL14.eglSwapBuffers(display, layer.surface);
            drain(layer);
        }
        if (previewW > 0 && now - previewAt > 66000000L) {
            previewAt = now;
            EGL14.eglMakeCurrent(display, pbuffer, pbuffer, eglContext);
            makePreview(previewW, previewH);
        }
        // 18.1: what the coders made of this picture is taken in a
        // moment, not with the next picture, a thirtieth of a second on.
        handler.removeCallbacks(takeCoded);
        handler.postDelayed(takeCoded, 8);
    }

    private final Runnable takeCoded = new Runnable() {
        @Override
        public void run() {
            try {
                for (Layer layer : layers) {
                    if (!failed && layer.encoder != null) {
                        drain(layer);
                    }
                }
            } catch (RuntimeException error) {
                fail("the video coder failed", error);
            }
        }
    };

    /** The camera's picture over the whole of what is drawn into, as
     * Android's matrix gives it (upright for the phone's natural way up),
     * then `unmirror`ed (the front camera's, for the one sent) and turned
     * `turn` degrees clockwise. */
    private void draw(int turn, boolean unmirror) {
        GLES20.glUseProgram(program);
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, oesTexture);
        float[] pos = new float[16];
        Matrix.setRotateM(pos, 0, -turn, 0f, 0f, 1f);
        if (unmirror) {
            Matrix.scaleM(pos, 0, -1f, 1f, 1f);
        }
        GLES20.glUniformMatrix4fv(uPos, 1, false, pos, 0);
        GLES20.glUniformMatrix4fv(uTex, 1, false, texMatrix, 0);
        GLES20.glEnableVertexAttribArray(aPos);
        GLES20.glVertexAttribPointer(aPos, 2, GLES20.GL_FLOAT, false, 0,
                QUAD);
        GLES20.glEnableVertexAttribArray(aTex);
        GLES20.glVertexAttribPointer(aTex, 2, GLES20.GL_FLOAT, false, 0,
                CORNERS);
        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4);
    }

    /** What a size's coder has made, into the queue for Python. */
    private void drain(Layer layer) {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        while (true) {
            int index = layer.encoder.dequeueOutputBuffer(info, 0);
            if (index == MediaCodec.INFO_TRY_AGAIN_LATER) {
                return;
            }
            if (index < 0) {
                continue;                    // its format, its buffers
            }
            ByteBuffer out = layer.encoder.getOutputBuffer(index);
            byte[] data = new byte[info.size];
            if (out != null) {
                out.position(info.offset);
                out.get(data, 0, info.size);
            }
            layer.encoder.releaseOutputBuffer(index, false);
            if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) {
                layer.settings = data;
                continue;
            }
            boolean key = (info.flags & MediaCodec.BUFFER_FLAG_KEY_FRAME)
                    != 0;
            if (key && layer.settings.length > 0) {
                byte[] whole = new byte[layer.settings.length + data.length];
                System.arraycopy(layer.settings, 0, whole, 0,
                        layer.settings.length);
                System.arraycopy(data, 0, whole, layer.settings.length,
                        data.length);
                data = whole;
            }
            coded.offer(layer.index + ":" + layer.codec + ":"
                    + (key ? "1" : "0") + ":"
                    + Base64.encodeToString(data, Base64.NO_WRAP));
        }
    }

    /** The coded pictures waiting, "|" between them; "" after timeoutMs
     * with none (Python asks with 0, and waits itself). */
    public String poll(int timeoutMs) {
        try {
            String first = coded.poll(timeoutMs, TimeUnit.MILLISECONDS);
            if (first == null) {
                return "";
            }
            StringBuilder all = new StringBuilder(first);
            String next;
            while ((next = coded.poll()) != null) {
                all.append('|').append(next);
            }
            return all.toString();
        } catch (InterruptedException error) {
            return "";
        }
    }

    // ---- this phone's own tile --------------------------------------------------
    /** This phone's own picture at its tile's size, "w:h:base64" RGBA rows
     * from the top, made a few times a second; "" when none is new. */
    public String preview(int w, int h) {
        previewW = w;
        previewH = h;
        String out = previewOut;
        previewOut = "";
        return out;
    }

    private void makePreview(int boxW, int boxH) {
        boolean upright = lying(shownTurn());
        int sw = upright ? captureSize.getHeight() : captureSize.getWidth();
        int sh = upright ? captureSize.getWidth() : captureSize.getHeight();
        double k = Math.min(1.0, Math.max(boxW / (double) sw,
                boxH / (double) sh));
        int w = Math.max(4, ((int) (sw * k)) / 4 * 4);
        int h = Math.max(2, ((int) (sh * k)) & ~1);
        if (fbo == 0 || fboW != w || fboH != h) {
            int[] ids = new int[1];
            if (fbo != 0) {
                GLES20.glDeleteFramebuffers(1, new int[]{fbo}, 0);
                GLES20.glDeleteTextures(1, new int[]{fboTexture}, 0);
            }
            GLES20.glGenTextures(1, ids, 0);
            fboTexture = ids[0];
            GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, fboTexture);
            GLES20.glTexImage2D(GLES20.GL_TEXTURE_2D, 0, GLES20.GL_RGBA, w,
                    h, 0, GLES20.GL_RGBA, GLES20.GL_UNSIGNED_BYTE, null);
            GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D,
                    GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR);
            GLES20.glGenFramebuffers(1, ids, 0);
            fbo = ids[0];
            GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, fbo);
            GLES20.glFramebufferTexture2D(GLES20.GL_FRAMEBUFFER,
                    GLES20.GL_COLOR_ATTACHMENT0, GLES20.GL_TEXTURE_2D,
                    fboTexture, 0);
            fboW = w;
            fboH = h;
        }
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, fbo);
        GLES20.glViewport(0, 0, w, h);
        draw(shownTurn(), false);
        ByteBuffer pixels = ByteBuffer.allocateDirect(w * h * 4);
        GLES20.glReadPixels(0, 0, w, h, GLES20.GL_RGBA,
                GLES20.GL_UNSIGNED_BYTE, pixels);
        GLES20.glBindFramebuffer(GLES20.GL_FRAMEBUFFER, 0);
        byte[] rows = new byte[w * h * 4];
        byte[] row = new byte[w * 4];
        for (int y = 0; y < h; y++) {               // read from the bottom
            pixels.position((h - 1 - y) * w * 4);
            pixels.get(row);
            System.arraycopy(row, 0, rows, y * w * 4, w * 4);
        }
        previewOut = w + ":" + h + ":"
                + Base64.encodeToString(rows, Base64.NO_WRAP);
    }

    public void close() {
        if (holding != null) {
            holding.disable();
        }
        handler.post(new Runnable() {
            @Override
            public void run() {
                try {
                    closeCamera();
                    for (Layer layer : layers) {
                        release(layer);
                    }
                    layers.clear();
                    if (cameraSurface != null) {
                        cameraSurface.release();
                    }
                    if (texture != null) {
                        texture.release();
                    }
                    if (display != EGL14.EGL_NO_DISPLAY) {
                        EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE,
                                EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT);
                        EGL14.eglDestroySurface(display, pbuffer);
                        EGL14.eglDestroyContext(display, eglContext);
                        EGL14.eglTerminate(display);
                        display = EGL14.EGL_NO_DISPLAY;
                    }
                } catch (RuntimeException error) {
                    lastError = "closing the video: " + error;   // 17.3
                } finally {
                    thread.quitSafely();
                }
            }
        });
    }

    // ---- the others' pictures ---------------------------------------------------
    /** One stream decoded by this phone's decoder for its codec, each
     * picture scaled (nearest pixel) to the tile it is drawn in. */
    public static class Decoder {
        private final MediaCodec codec;
        private long at = 0;
        private int pictures = 0;
        private String refused = "";

        public Decoder(String name) throws Exception {
            String mime = mimeOf(name);
            MediaCodec made;
            try {
                made = started(mime, true);
            } catch (Exception refused) {
                made = started(mime, false);        // as before 18.1
            }
            codec = made;
        }

        /** A decoder, started. prompt (18.1): each picture out as it is
         * decoded, not held for ones that might come before it (a
         * call's never do), where the decoder says it can (Android 11
         * on). One asked for what it cannot do refuses to be configured
         * (Devices run 24, Android 11: no picture decoded at all), so
         * it is asked only where it says so, and made again without if
         * it refuses even then. */
        private static MediaCodec started(String mime, boolean prompt)
                throws Exception {
            MediaCodec codec = MediaCodec.createDecoderByType(mime);
            try {
                MediaFormat format =
                        MediaFormat.createVideoFormat(mime, 640, 360);
                format.setInteger(MediaFormat.KEY_COLOR_FORMAT,
                        MediaCodecInfo.CodecCapabilities
                                .COLOR_FormatYUV420Flexible);
                if (prompt && Build.VERSION.SDK_INT >= 30
                        && codec.getCodecInfo().getCapabilitiesForType(mime)
                                .isFeatureSupported(MediaCodecInfo
                                        .CodecCapabilities.FEATURE_LowLatency)) {
                    format.setInteger(MediaFormat.KEY_LOW_LATENCY, 1);
                }
                codec.configure(format, null, null, 0);
                codec.start();
                return codec;
            } catch (Exception error) {
                codec.release();
                throw error;
            }
        }

        /** "w:h:base64" RGBA rows of the newest picture decoded, drawn in
         * a w × h tile (fill: cover it; else whole); "" when none came yet
         * (a decoder hands a picture out a call later), or w is 0
         * (decoded, not drawn); "!" for a damaged picture, or one there
         * was no room for (the decoder still busy): the pictures after
         * either need a key frame (17.2.1; before, that one was dropped
         * unsaid). An empty b64 hands nothing in, and gives what came out
         * late. It waits 2 ms at most: pyjnius may hold Python's lock
         * while Java runs. The first picture alone waits up to 100 ms:
         * a decoder just started is not ready at once, and that picture
         * is the key frame every one after it needs (Devices run 25, a
         * slow emulator: it was refused, and none could be decoded). */
        public String decode(String b64, int w, int h, boolean fill) {
            try {
                byte[] data = Base64.decode(b64, Base64.NO_WRAP);
                if (data.length > 0) {
                    int in = codec.dequeueInputBuffer(
                            at == 0 ? 100000 : 2000);
                    if (in < 0) {
                        refused = "no room in the decoder";
                        return "!";
                    }
                    ByteBuffer buffer = codec.getInputBuffer(in);
                    buffer.clear();
                    buffer.put(data);
                    codec.queueInputBuffer(in, 0, data.length, at, 0);
                    at += 33333;
                }
                String out = "";
                MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
                while (true) {
                    int index = codec.dequeueOutputBuffer(info, 0);
                    if (index == MediaCodec.INFO_TRY_AGAIN_LATER) {
                        return out;
                    }
                    if (index < 0) {
                        continue;
                    }
                    if (w > 0 && h > 0) {
                        Image image = codec.getOutputImage(index);
                        if (image != null) {
                            out = rgba(image, w, h, fill);
                            image.close();
                        }
                    }
                    codec.releaseOutputBuffer(index, false);
                    pictures++;
                }
            } catch (Exception error) {
                refused = error.toString();
                return "!";
            }
        }

        /** Why decode last gave "!", for the log and the self-test. */
        public String refused() {
            return refused;
        }

        /** Every picture decoded so far, drawn or not: decode gives the
         * newest alone, and the self-test counts them all. */
        public int pictures() {
            return pictures;
        }

        public void close() {
            try {
                codec.stop();
            } catch (Exception ignored) {
                // released below either way
            }
            codec.release();
        }
    }

    /** A YUV picture as RGBA rows at the size it is drawn (main.py's
     * video_fit), BT.601 as phones' cameras code it. */
    static String rgba(Image image, int boxW, int boxH, boolean fill) {
        int sw = image.getWidth(), sh = image.getHeight();
        double k = fill ? Math.max(boxW / (double) sw, boxH / (double) sh)
                : Math.min(boxW / (double) sw, boxH / (double) sh);
        k = Math.min(1.0, k);
        int w = Math.max(4, ((int) (sw * k)) / 4 * 4);
        int h = Math.max(2, ((int) (sh * k)) & ~1);
        Image.Plane[] planes = image.getPlanes();
        ByteBuffer yb = planes[0].getBuffer();
        ByteBuffer ub = planes[1].getBuffer();
        ByteBuffer vb = planes[2].getBuffer();
        int yRow = planes[0].getRowStride(), yStep = planes[0].getPixelStride();
        int uRow = planes[1].getRowStride(), uStep = planes[1].getPixelStride();
        int vRow = planes[2].getRowStride(), vStep = planes[2].getPixelStride();
        byte[] out = new byte[w * h * 4];
        int o = 0;
        for (int y = 0; y < h; y++) {
            int sy = y * sh / h;
            for (int x = 0; x < w; x++) {
                int sx = x * sw / w;
                int luma = (yb.get(sy * yRow + sx * yStep) & 0xff) - 16;
                int u = (ub.get((sy / 2) * uRow + (sx / 2) * uStep) & 0xff)
                        - 128;
                int v = (vb.get((sy / 2) * vRow + (sx / 2) * vStep) & 0xff)
                        - 128;
                int c = 298 * Math.max(0, luma) + 128;
                out[o++] = clamp((c + 409 * v) >> 8);
                out[o++] = clamp((c - 100 * u - 208 * v) >> 8);
                out[o++] = clamp((c + 516 * u) >> 8);
                out[o++] = (byte) 255;
            }
        }
        return w + ":" + h + ":" + Base64.encodeToString(out, Base64.NO_WRAP);
    }

    private static byte clamp(int value) {
        return (byte) (value < 0 ? 0 : (value > 255 ? 255 : value));
    }
}
