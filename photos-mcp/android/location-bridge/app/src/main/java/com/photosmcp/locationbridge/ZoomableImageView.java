package com.photosmcp.locationbridge;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Matrix;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;
import android.view.GestureDetector;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.widget.ImageView;

/** A bounded photo viewer: pinch/double-tap zoom, pan, and page fling at base scale. */
final class ZoomableImageView extends ImageView {
    interface NavigationListener {
        void onNavigateRequested(int offset);
        void onZoomChanged(float scale);
    }

    private static final float MIN_SCALE = 1f;
    private static final float MAX_SCALE = 4f;
    private static final float DOUBLE_TAP_SCALE = 2.5f;

    private final Matrix drawMatrix = new Matrix();
    private final RectF drawableBounds = new RectF();
    private final RectF displayedBounds = new RectF();
    private final ScaleGestureDetector scaleDetector;
    private final GestureDetector gestureDetector;
    private final float flingVelocity;
    private final float flingDistance;
    private NavigationListener navigationListener;
    private float scale = MIN_SCALE;
    private boolean baseReady;

    ZoomableImageView(Context context) {
        super(context);
        super.setScaleType(ScaleType.MATRIX);
        setClickable(true);
        setFocusable(true);
        float density = getResources().getDisplayMetrics().density;
        flingVelocity = 500f * density;
        flingDistance = 48f * density;
        scaleDetector = new ScaleGestureDetector(context, new ScaleListener());
        gestureDetector = new GestureDetector(context, new GestureListener());
    }

    void setNavigationListener(NavigationListener listener) {
        navigationListener = listener;
    }

    float getZoom() {
        return scale;
    }

    void zoomBy(float factor) {
        if (!baseReady) return;
        applyScale(factor, getWidth() / 2f, getHeight() / 2f);
    }

    void resetZoom() {
        scale = MIN_SCALE;
        configureBaseMatrix();
        notifyZoomChanged();
    }

    @Override public void setImageBitmap(Bitmap bitmap) {
        super.setImageBitmap(bitmap);
        baseReady = false;
        post(this::resetZoom);
    }

    @Override public void setImageDrawable(Drawable drawable) {
        super.setImageDrawable(drawable);
        baseReady = false;
        if (drawable == null) {
            scale = MIN_SCALE;
            drawMatrix.reset();
            setImageMatrix(drawMatrix);
            notifyZoomChanged();
        } else {
            post(this::resetZoom);
        }
    }

    @Override protected void onSizeChanged(int width, int height, int oldWidth, int oldHeight) {
        super.onSizeChanged(width, height, oldWidth, oldHeight);
        post(this::resetZoom);
    }

    @Override public boolean onTouchEvent(MotionEvent event) {
        if (event.getActionMasked() == MotionEvent.ACTION_DOWN && getParent() != null) {
            getParent().requestDisallowInterceptTouchEvent(true);
        }
        scaleDetector.onTouchEvent(event);
        gestureDetector.onTouchEvent(event);
        if (event.getActionMasked() == MotionEvent.ACTION_UP
                || event.getActionMasked() == MotionEvent.ACTION_CANCEL) {
            constrainTranslation();
            performClick();
        }
        return true;
    }

    @Override public boolean performClick() {
        super.performClick();
        return true;
    }

    private void configureBaseMatrix() {
        Drawable drawable = getDrawable();
        int width = getWidth() - getPaddingLeft() - getPaddingRight();
        int height = getHeight() - getPaddingTop() - getPaddingBottom();
        if (drawable == null || width <= 0 || height <= 0
                || drawable.getIntrinsicWidth() <= 0 || drawable.getIntrinsicHeight() <= 0) {
            baseReady = false;
            drawMatrix.reset();
            setImageMatrix(drawMatrix);
            return;
        }
        float drawableWidth = drawable.getIntrinsicWidth();
        float drawableHeight = drawable.getIntrinsicHeight();
        float fit = Math.min(width / drawableWidth, height / drawableHeight);
        float fittedWidth = drawableWidth * fit;
        float fittedHeight = drawableHeight * fit;
        float left = getPaddingLeft() + (width - fittedWidth) / 2f;
        float top = getPaddingTop() + (height - fittedHeight) / 2f;
        drawMatrix.reset();
        drawMatrix.postScale(fit, fit);
        drawMatrix.postTranslate(left, top);
        drawableBounds.set(0, 0, drawableWidth, drawableHeight);
        baseReady = true;
        setImageMatrix(drawMatrix);
    }

    private void applyScale(float factor, float focusX, float focusY) {
        float next = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * factor));
        float applied = next / scale;
        if (Math.abs(applied - 1f) < 0.001f) return;
        drawMatrix.postScale(applied, applied, focusX, focusY);
        scale = next;
        constrainTranslation();
        notifyZoomChanged();
    }

    private void constrainTranslation() {
        if (!baseReady) return;
        displayedBounds.set(drawableBounds);
        drawMatrix.mapRect(displayedBounds);
        float viewLeft = getPaddingLeft();
        float viewTop = getPaddingTop();
        float viewRight = getWidth() - getPaddingRight();
        float viewBottom = getHeight() - getPaddingBottom();
        float dx;
        float dy;
        if (displayedBounds.width() <= viewRight - viewLeft) {
            dx = (viewLeft + viewRight) / 2f - displayedBounds.centerX();
        } else if (displayedBounds.left > viewLeft) {
            dx = viewLeft - displayedBounds.left;
        } else if (displayedBounds.right < viewRight) {
            dx = viewRight - displayedBounds.right;
        } else {
            dx = 0;
        }
        if (displayedBounds.height() <= viewBottom - viewTop) {
            dy = (viewTop + viewBottom) / 2f - displayedBounds.centerY();
        } else if (displayedBounds.top > viewTop) {
            dy = viewTop - displayedBounds.top;
        } else if (displayedBounds.bottom < viewBottom) {
            dy = viewBottom - displayedBounds.bottom;
        } else {
            dy = 0;
        }
        drawMatrix.postTranslate(dx, dy);
        setImageMatrix(drawMatrix);
    }

    private void notifyZoomChanged() {
        if (navigationListener != null) navigationListener.onZoomChanged(scale);
    }

    private final class ScaleListener extends ScaleGestureDetector.SimpleOnScaleGestureListener {
        @Override public boolean onScaleBegin(ScaleGestureDetector detector) {
            return baseReady;
        }

        @Override public boolean onScale(ScaleGestureDetector detector) {
            applyScale(detector.getScaleFactor(), detector.getFocusX(), detector.getFocusY());
            return true;
        }
    }

    private final class GestureListener extends GestureDetector.SimpleOnGestureListener {
        @Override public boolean onDown(MotionEvent event) {
            return true;
        }

        @Override public boolean onDoubleTap(MotionEvent event) {
            if (scale > MIN_SCALE + 0.05f) {
                resetZoom();
            } else {
                applyScale(DOUBLE_TAP_SCALE, event.getX(), event.getY());
            }
            return true;
        }

        @Override public boolean onScroll(
                MotionEvent first, MotionEvent current, float distanceX, float distanceY) {
            if (!baseReady || scale <= MIN_SCALE + 0.01f || scaleDetector.isInProgress()) {
                return false;
            }
            drawMatrix.postTranslate(-distanceX, -distanceY);
            constrainTranslation();
            return true;
        }

        @Override public boolean onFling(
                MotionEvent first, MotionEvent current, float velocityX, float velocityY) {
            if (scale > MIN_SCALE + 0.01f || navigationListener == null) return false;
            float distanceX = current.getX() - first.getX();
            float distanceY = current.getY() - first.getY();
            if (Math.abs(distanceX) < flingDistance
                    || Math.abs(velocityX) < flingVelocity
                    || Math.abs(distanceX) <= Math.abs(distanceY) * 1.25f
                    || Math.abs(velocityX) <= Math.abs(velocityY) * 1.1f) {
                return false;
            }
            navigationListener.onNavigateRequested(velocityX < 0 ? 1 : -1);
            return true;
        }
    }
}
