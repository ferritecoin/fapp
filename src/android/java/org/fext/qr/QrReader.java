package org.fext.qr;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;

import com.google.zxing.BinaryBitmap;
import com.google.zxing.DecodeHintType;
import com.google.zxing.LuminanceSource;
import com.google.zxing.RGBLuminanceSource;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;

import java.util.EnumMap;
import java.util.Map;

/**
 * Reads the QR code in a photo, for main.py's "scan their code" when two
 * people verify each other (11.0): the camera app takes the photo, this
 * finds the code in it (ZXing), on a copy no more than 1600 pixels across.
 * A code drawn light on dark is read too.
 */
public final class QrReader {
    private QrReader() {
    }

    /** The text of the QR code in the photo at `path`, or null. */
    public static String read(String path) {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(path, bounds);
        int side = Math.max(bounds.outWidth, bounds.outHeight);
        if (side <= 0) {
            return null;
        }
        int sample = 1;
        while (side / sample > 1600) {
            sample *= 2;
        }
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = sample;
        Bitmap bitmap = BitmapFactory.decodeFile(path, options);
        if (bitmap == null) {
            return null;
        }
        int w = bitmap.getWidth();
        int h = bitmap.getHeight();
        int[] pixels = new int[w * h];
        bitmap.getPixels(pixels, 0, w, 0, 0, w, h);
        bitmap.recycle();
        LuminanceSource source = new RGBLuminanceSource(w, h, pixels);
        Map<DecodeHintType, Object> hints =
                new EnumMap<DecodeHintType, Object>(DecodeHintType.class);
        hints.put(DecodeHintType.TRY_HARDER, Boolean.TRUE);
        String found = decode(source, hints);
        return found != null ? found : decode(source.invert(), hints);
    }

    private static String decode(LuminanceSource source,
                                 Map<DecodeHintType, Object> hints) {
        try {
            return new QRCodeReader().decode(
                    new BinaryBitmap(new HybridBinarizer(source)), hints)
                    .getText();
        } catch (Exception e) {
            return null;
        }
    }
}
