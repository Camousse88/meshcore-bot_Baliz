import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = fs.readFileSync(new URL('../../modules/web_viewer/templates/time.html', import.meta.url), 'utf8');
const start = html.indexOf('    class TimeViewer');
const end = html.lastIndexOf("    document.addEventListener('DOMContentLoaded'");
assert.ok(start >= 0 && end > start);
const container = { innerHTML: '' };
const now = Date.parse('2026-11-05T14:03:32Z');
class FixedDate extends Date {
    static now() { return now; }
}
const context = vm.createContext({ Date: FixedDate, document: {
    getElementById: () => container, querySelectorAll: () => [],
} });
vm.runInContext(html.slice(start, end) + '\nglobalThis.TimeClass = TimeViewer;', context);
const viewer = Object.create(context.TimeClass.prototype);
viewer.escapeHtml = value => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;');

test('UTC sync time is minutes ago, not an hour ago, in the French timezone', () => {
    assert.equal(viewer.formatTimestamp('2026-11-05T14:00:45+00:00'), 'il y a 2 min');
    assert.equal(viewer.formatTimestamp('2026-11-05 14:00:45'), 'il y a 2 min');
    assert.equal(viewer.timestampDate('2026-11-05 14:00:45').toISOString(), '2026-11-05T14:00:45.000Z');
});

test('Concarneau ACK with old unset clock has no OK badge or huge duration', () => {
    viewer.updateTargets([{ id: 1, target: 'ab'.repeat(32), contact_name: 'Concarneau', enabled: true,
        clock_state: 'awaiting_verification', clock_timestamp_valid: false, clock_drift: null,
        clock_observed_at: Date.parse('2026-11-05T00:58:15Z') / 1000, clock_source: 'advert',
        last_sync_success: true, last_sync_at: '2026-11-05T14:01:10+00:00',
    }]);
    assert.match(container.innerHTML, /Horloge à vérifier/);
    assert.match(container.innerHTML, /Réception confirmée/);
    assert.match(container.innerHTML, /Horloge non réglée/);
    assert.match(container.innerHTML, /Mesuré le/);
    assert.doesNotMatch(container.innerHTML, />\s*OK\s*</);
    assert.doesNotMatch(container.innerHTML, /1793177892|20754d/);
});

test('Trédudon good measurement and earlier missing ACK are presented separately', () => {
    viewer.updateTargets([{ id: 2, target: 'cd'.repeat(32), contact_name: 'Trédudon', enabled: true,
        clock_state: 'in_sync', clock_timestamp_valid: true, clock_drift: 9,
        clock_observed_at: Date.parse('2026-11-05T11:52:11Z') / 1000, clock_source: 'message',
        last_sync_success: false, last_sync_at: '2026-11-05T11:53:23+00:00',
    }]);
    assert.match(container.innerHTML, /À l’heure/);
    assert.match(container.innerHTML, /Réception non confirmée/);
    assert.match(container.innerHTML, />9s</);
    assert.doesNotMatch(container.innerHTML, /3591s|>\s*Fail\s*</);
});

test('clock rejection and stale measurements cannot render as success', () => {
    assert.match(viewer.clockStatusBadge('rejected'), /Commande refusée/);
    assert.match(viewer.clockStatusBadge('stale'), /Mesure ancienne/);
    assert.doesNotMatch(viewer.clockStatusBadge('rejected'), /bg-success|OK/);
});
