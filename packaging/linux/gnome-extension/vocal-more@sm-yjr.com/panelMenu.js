import St from 'gi://St';
import GObject from 'gi://GObject';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const _MODES = [
    ['walkie_talkie', 'Walkie-Talkie'],
    ['realtime_long', 'Long Dictation'],
    ['meeting', 'Meeting'],
];

export const VocalPanelIndicator = GObject.registerClass(
class VocalPanelIndicator extends PanelMenu.Button {
    _init(settings, client) {
        super._init(0.0, 'Vocal More', false);
        this._settings = settings;
        this._client = client;
        this._snapshot = null;
        this._available = false;

        this._icon = new St.Icon({
            icon_name: 'audio-input-microphone-symbolic',
            style_class: 'system-status-icon',
        });
        this.add_child(this._icon);

        this._statusItem = new PopupMenu.PopupMenuItem('Connecting…', {reactive: false});
        this.menu.addMenuItem(this._statusItem);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._toggleItem = new PopupMenu.PopupMenuItem('Start dictation');
        this._toggleItem.connect('activate', () => this._toggle());
        this.menu.addMenuItem(this._toggleItem);

        this._cancelItem = new PopupMenu.PopupMenuItem('Cancel');
        this._cancelItem.connect('activate', () => this._client.cancel());
        this.menu.addMenuItem(this._cancelItem);

        const modeMenu = new PopupMenu.PopupSubMenuMenuItem('Mode');
        this._modeItems = new Map();
        for (const [value, label] of _MODES) {
            const item = new PopupMenu.PopupMenuItem(label);
            item.connect('activate', () => {
                this._client.setMode(value);
                try { this._settings.set_string('mode', value); } catch (_error) { /* host owns persistence */ }
            });
            modeMenu.menu.addMenuItem(item);
            this._modeItems.set(value, item);
        }
        this.menu.addMenuItem(modeMenu);

        this._autoPasteItem = new PopupMenu.PopupSwitchMenuItem('Auto-paste', true);
        this._autoPasteItem.connect('toggled', (_item, state) => {
            this._client.setAutoPaste(state);
            try { this._settings.set_boolean('auto-paste', state); } catch (_error) { /* host owns persistence */ }
        });
        this.menu.addMenuItem(this._autoPasteItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._settingsItem = new PopupMenu.PopupMenuItem('Settings…');
        this._settingsItem.connect('activate', () => this._client.showSettings());
        this.menu.addMenuItem(this._settingsItem);
        this._quitItem = new PopupMenu.PopupMenuItem('Quit Vocal More');
        this._quitItem.connect('activate', () => this._client.quit());
        this.menu.addMenuItem(this._quitItem);

        this._applySnapshot(null);
    }

    update(snapshot) {
        this._snapshot = snapshot;
        this._applySnapshot(snapshot);
    }

    setAvailable(available) {
        this._available = available === true;
        this._applySnapshot(this._snapshot);
    }

    destroy() {
        super.destroy();
    }

    _toggle() {
        if (!this._snapshot || this._snapshot.state === 'idle' || this._snapshot.state === 'success' || this._snapshot.state === 'error') {
            this._client.triggerPressed();
        } else if (this._snapshot.state === 'recording') {
            if (this._snapshot.mode === 'walkie_talkie')
                this._client.triggerReleased();
            else
                this._client.triggerPressed();
        } else {
            this._client.cancel();
        }
    }

    _applySnapshot(snapshot) {
        if (!this._available) {
            this._statusItem.label.text = 'Backend unavailable';
            this._toggleItem.label.text = 'Start desktop service';
            this._toggleItem.setSensitive(false);
            this._cancelItem.setSensitive(false);
            return;
        }
        if (!snapshot) {
            this._statusItem.label.text = 'Ready';
            this._toggleItem.label.text = 'Start dictation';
            this._toggleItem.setSensitive(true);
            this._cancelItem.setSensitive(false);
            return;
        }
        const active = snapshot.state === 'recording' || snapshot.state === 'processing';
        this._statusItem.label.text = active ? 'Working' : 'Ready';
        this._toggleItem.label.text = active ? 'Stop dictation' : `Start dictation (${snapshot.trigger_label})`;
        this._toggleItem.setSensitive(true);
        this._cancelItem.setSensitive(snapshot.can_cancel === true);
        this._autoPasteItem.setToggleState(snapshot.auto_paste !== false);
        for (const [value, item] of this._modeItems)
            item.setOrnament(snapshot.mode === value ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
    }
});
