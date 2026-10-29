// SPDX-License-Identifier: GPL-3.0-only
// Pure, bounded protocol shared by the Shell adapter and its regression tests.
export function decode(text) {
    if (typeof text !== 'string' || text.length > 8192)
        throw new Error('Invalid capsule message');
    const value = JSON.parse(text);
    if (!value || !['hidden', 'starting', 'recording', 'processing', 'cancelling', 'error'].includes(value.state))
        throw new Error('Invalid capsule state');
    return {
        active: value.active === true,
        state: value.state,
        text: typeof value.text === 'string' ? [...value.text].slice(0, 200).join('') : '',
        level: Number.isFinite(value.level) ? Math.max(0, Math.min(1, value.level)) : 0,
        english: value.english === true,
        reduced_motion: value.reduced_motion === true,
    };
}
