package org.fext.call;

import android.media.projection.MediaProjection;

/** 18.0 (PHONESHARE): Android stopped sharing the screen. Android 14 wants
 * a MediaProjection.Callback registered before the screen is captured,
 * and that is an abstract class, which pyjnius cannot extend: this one
 * passes onStop to a Runnable written in Python (main.py,
 * AndroidScreenCoder._ended). Everything else is in Python. */
public class ProjectionStop extends MediaProjection.Callback {
    private final Runnable then;

    public ProjectionStop(Runnable then) {
        this.then = then;
    }

    @Override
    public void onStop() {
        then.run();
    }
}
