import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const _STATE_LABELS = {
    recording: 'Recording',
    processing: 'Processing',
    success: 'Done',
    error: 'Could not finish',
    failed: 'Could not finish',
    cancelled: 'Cancelled',
};

const _MODE_LABELS = {
    walkie_talkie: 'Walkie-Talkie',
    realtime_long: 'Long Dictation',
    meeting: 'Meeting',
};

export const VocalCapsule = GObject.registerClass(
class VocalCapsule extends St.BoxLayout {
    _init(onCancel) {
        super._init({
            style_class: 'vocal-more-capsule',
            vertical: false,
            reactive: false,
            can_focus: false,
            track_hover: false,
        });
        this._onCancel = onCancel;
        this._snapshot = null;
        this._reducedMotion = false;
        this._cancelButton = null;
        this._smoothedLevel = 0;
        this._processingSource = 0;
        this._processingStartedAt = 0;

        this.set_style([
            'border-radius: 24px',
            'box-shadow: 0px 4px 18px rgba(0, 0, 0, 0.45)',
        ].join(';'));
        this.set_pivot_point(0.5, 0.5);

        // St accepts one shadow per actor. Nesting the directional shadow
        // inside the ambient one gives the intended two-layer depth without
        // relying on unsupported comma-separated CSS values.
        this._surface = new St.BoxLayout({
            vertical: false,
            reactive: false,
            can_focus: false,
            track_hover: false,
            style: [
                'background-color: rgba(0, 0, 0, 1)',
                'border: 1px solid rgba(255, 255, 255, 0.32)',
                'border-radius: 24px',
                'padding: 8px 12px',
                'spacing: 8px',
                'min-width: 220px',
                'box-shadow: 0px 1px 4px rgba(0, 0, 0, 0.7)',
            ].join(';'),
        });
        this.add_child(this._surface);

        const content = new St.BoxLayout({vertical: true, reactive: false, can_focus: false});
        this._surface.add_child(content);
        this._primary = new St.Label({
            text: 'Vocal More',
            style: 'color: rgba(255,255,255,0.92); font-weight: 600;',
            reactive: false,
            can_focus: false,
        });
        this._secondary = new St.Label({
            text: 'Ready',
            style: 'color: rgba(255,255,255,0.55); font-size: 0.85em;',
            reactive: false,
            can_focus: false,
        });
        content.add_child(this._primary);
        content.add_child(this._secondary);

        this._progressTrack = new St.Widget({
            style: 'background-color: rgba(255,255,255,0.16); border-radius: 1px; width: 84px; height: 2px;',
            reactive: false,
            can_focus: false,
        });
        this._progressFill = new St.Widget({
            style: 'background-color: rgba(255,255,255,0.78); border-radius: 1px; height: 2px;',
            reactive: false,
            can_focus: false,
        });
        this._progressTrack.add_child(this._progressFill);
        content.add_child(this._progressTrack);
        this._progressTrack.hide();

        this._waveform = new St.BoxLayout({
            vertical: false,
            style: 'spacing: 2px; min-width: 36px; min-height: 20px;',
            reactive: false,
            can_focus: false,
        });
        this._bars = [];
        for (let index = 0; index < 7; index++) {
            const bar = new St.Widget({
                style: 'background-color: rgba(255,255,255,0.8); border-radius: 2px; width: 3px; height: 4px;',
                reactive: false,
                can_focus: false,
                y_align: Clutter.ActorAlign.CENTER,
            });
            this._bars.push(bar);
            this._waveform.add_child(bar);
        }
        this._surface.add_child(this._waveform);

        this._cancelButton = new St.Button({
            style: 'background-color: rgba(255,59,48,0.95); border-radius: 12px; padding: 4px;',
            child: new St.Icon({icon_name: 'window-close-symbolic', icon_size: 14}),
            reactive: true,
            can_focus: false,
            track_hover: true,
        });
        this._cancelButton.connect('clicked', () => {
            if (this._onCancel)
                this._onCancel();
        });
        this._surface.add_child(this._cancelButton);
        this._cancelButton.hide();
        this.connect('destroy', () => this._stopProcessingAnimation());
        this.hide();
    }

    setReducedMotion(reduced) {
        this._reducedMotion = reduced === true;
        if (this._snapshot?.state === 'processing')
            this._startProcessingAnimation();
    }

    update(snapshot) {
        this._snapshot = snapshot;
        if (!snapshot || snapshot.state === 'idle') {
            this._setVisible(false);
            return;
        }
        const state = _STATE_LABELS[snapshot.state] || 'Vocal More';
        const mode = _MODE_LABELS[snapshot.mode] || 'Dictation';
        this._primary.set_text(state);
        this._secondary.set_text(`${mode} · ${snapshot.trigger_label}`);
        this._cancelButton.visible = snapshot.can_cancel === true;
        this._setWaveform(snapshot.audio_level, snapshot.state === 'recording');
        if (snapshot.state === 'processing')
            this._startProcessingAnimation();
        else
            this._stopProcessingAnimation();
        this._setVisible(true);
        this.reposition();
    }

    setBackendAvailable(available) {
        if (available) {
            if (this._snapshot)
                this.update(this._snapshot);
            return;
        }
        this._primary.set_text('Vocal More unavailable');
        this._secondary.set_text('Start the desktop service to reconnect');
        this._cancelButton.hide();
        this._setWaveform(0, false);
        this._stopProcessingAnimation();
        this._setVisible(true);
        this.reposition();
    }

    reposition() {
        if (!this.visible)
            return;
        let monitor = Main.layoutManager.primaryMonitor;
        try {
            const focused = global.display.focus_window;
            const index = focused ? focused.get_monitor() : -1;
            if (index >= 0 && Main.layoutManager.monitors[index])
                monitor = Main.layoutManager.monitors[index];
        } catch (_error) {
            // Primary-monitor placement remains a safe fallback during hotplug.
        }
        if (!monitor)
            return;
        const [, naturalWidth] = this.get_preferred_width(-1);
        const [, naturalHeight] = this.get_preferred_height(naturalWidth);
        this.set_position(
            Math.round(monitor.x + (monitor.width - naturalWidth) / 2),
            Math.round(monitor.y + 16));
        this.set_size(naturalWidth, naturalHeight);
    }

    _setWaveform(level, active) {
        const target = active ? Math.max(0, Math.min(1, Number(level) || 0)) : 0;
        const smoothing = target >= this._smoothedLevel ? 0.78 : 0.18;
        this._smoothedLevel += (target - this._smoothedLevel) * smoothing;
        const amplitude = this._smoothedLevel;
        this._bars.forEach((bar, index) => {
            const envelope = 0.35 + (1 - Math.abs(index - 3) / 4) * 0.65;
            const height = active ? Math.max(4, Math.round(4 + amplitude * envelope * 16)) : 4;
            bar.set_style(`background-color: rgba(255,255,255,${active ? 0.8 : 0.4}); border-radius: 2px; width: 3px; height: ${height}px;`);
        });
    }

    _startProcessingAnimation() {
        this._progressTrack.show();
        if (!this._processingStartedAt)
            this._processingStartedAt = GLib.get_monotonic_time();
        if (this._reducedMotion) {
            if (this._processingSource) {
                GLib.Source.remove(this._processingSource);
                this._processingSource = 0;
            }
            this._progressFill.set_width(50);
            this._primary.opacity = 255;
            return;
        }
        if (this._processingSource)
            return;
        this._processingSource = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 50, () => {
            if (!this._snapshot || this._snapshot.state !== 'processing') {
                this._processingSource = 0;
                return GLib.SOURCE_REMOVE;
            }
            const seconds = (GLib.get_monotonic_time() - this._processingStartedAt) / 1_000_000;
            // Approach 92% asymptotically so the UI never promises a finish
            // time it cannot know. A restrained luminance sweep is the Shell
            // equivalent of the processing shimmer used by the macOS pill.
            const progress = Math.min(0.92, 0.92 * (1 - Math.exp(-seconds / 5.5)));
            this._progressFill.set_width(Math.max(3, Math.round(84 * progress)));
            this._primary.opacity = Math.round(205 + 50 * (0.5 + 0.5 * Math.sin(seconds * 3.2)));
            return GLib.SOURCE_CONTINUE;
        });
    }

    _stopProcessingAnimation() {
        if (this._processingSource) {
            GLib.Source.remove(this._processingSource);
            this._processingSource = 0;
        }
        this._processingStartedAt = 0;
        this._primary.opacity = 255;
        this._progressFill.set_width(0);
        this._progressTrack.hide();
    }

    _setVisible(visible) {
        if (visible === this.visible)
            return;
        if (visible) {
            this.show();
            if (this._reducedMotion) {
                this.opacity = 255;
                this.scale_x = 1;
                this.scale_y = 1;
            } else {
                this.opacity = 0;
                this.scale_x = 0.96;
                this.scale_y = 0.96;
                this.ease({opacity: 255, scale_x: 1, scale_y: 1, duration: 160,
                    mode: Clutter.AnimationMode.EASE_OUT_QUAD});
            }
        } else if (this._reducedMotion) {
            this.hide();
        } else {
            this.ease({opacity: 0, scale_x: 0.96, scale_y: 0.96, duration: 120,
                mode: Clutter.AnimationMode.EASE_IN_QUAD,
                onComplete: () => this.hide()});
        }
    }
});
