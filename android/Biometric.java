package org.fext.lock;

import android.app.Activity;
import android.content.DialogInterface;
import android.hardware.biometrics.BiometricManager;
import android.hardware.biometrics.BiometricPrompt;
import android.os.Build;
import android.os.CancellationSignal;

/**
 * The app lock's fingerprint or face (16.2, I1): Android's own prompt,
 * its answer handed back to Python through {@link Result} (pyjnius).
 * Android 10 (API 29) and later; before it, never available, and the
 * code alone opens the app.
 */
public class Biometric {

    /** What the app implements: opened, or why not. */
    public interface Result {
        void done(boolean ok, String why);
    }

    /** Is a fingerprint or face enrolled and usable now? */
    public static boolean available(Activity activity) {
        if (Build.VERSION.SDK_INT < 29) {
            return false;
        }
        BiometricManager manager =
                activity.getSystemService(BiometricManager.class);
        if (manager == null) {
            return false;
        }
        if (Build.VERSION.SDK_INT >= 30) {
            return manager.canAuthenticate(
                    BiometricManager.Authenticators.BIOMETRIC_WEAK)
                    == BiometricManager.BIOMETRIC_SUCCESS;
        }
        return manager.canAuthenticate()
                == BiometricManager.BIOMETRIC_SUCCESS;
    }

    /** Show the prompt; `result` is told once, on the main thread. */
    public static void ask(final Activity activity, final String title,
                           final String cancel, final Result result) {
        if (Build.VERSION.SDK_INT < 29) {
            result.done(false, "unavailable");
            return;
        }
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    BiometricPrompt prompt = new BiometricPrompt.Builder(
                            activity)
                            .setTitle(title)
                            .setNegativeButton(cancel,
                                    activity.getMainExecutor(),
                                    new DialogInterface.OnClickListener() {
                                        @Override
                                        public void onClick(
                                                DialogInterface dialog,
                                                int which) {
                                            result.done(false, "cancel");
                                        }
                                    })
                            .build();
                    prompt.authenticate(new CancellationSignal(),
                            activity.getMainExecutor(),
                            new BiometricPrompt.AuthenticationCallback() {
                                @Override
                                public void onAuthenticationSucceeded(
                                        BiometricPrompt.AuthenticationResult
                                                answer) {
                                    result.done(true, "");
                                }

                                @Override
                                public void onAuthenticationError(
                                        int code, CharSequence message) {
                                    result.done(false,
                                            String.valueOf(message));
                                }
                            });
                } catch (Exception error) {
                    result.done(false, String.valueOf(error));
                }
            }
        });
    }
}
