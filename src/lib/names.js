import { BkkLib } from '../lib/core.js';

Object.assign(BkkLib, {
  esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  },
  norm(name) {
    let t = String(name || '').normalize('NFC');
    t = t.replace(/[\u200B-\u200D\uFEFF]/g, '');
    t = t.replace(/[\u00A0\u202F]/g, ' ');
    t = t.trim().toLowerCase();
    t = t.replace(/[\u2013\u2014]/g, '-');
    t = t.replace(/\s+/g, ' ');
    [' vas\u00fat\u00e1llom\u00e1s', ' p\u00e1lyaudvar', ' pu.'].forEach((sfx) => {
      if (t.endsWith(sfx)) t = t.slice(0, -sfx.length).trim();
    });
    t = t.replace(/[,.;:\s]+$/g, '').trim();
    return t;
  },
  /* "Keleti pályaudvar M" (street) and "Keleti pályaudvar" (metro) are one place. */
  stationKey(name) {
    let t = String(name || '').normalize('NFC');
    t = t.replace(/[\u200B-\u200D\uFEFF]/g, '');
    t = t.replace(/[\u00A0\u202F]/g, ' ');
    t = t.trim().toLowerCase();
    t = t.replace(/[\u2013\u2014]/g, '-');
    t = t.replace(/\s+/g, ' ');
    t = t.replace(/\s+m\+h$/i, '').trim();
    t = t.replace(/\s+m\+$/i, '').trim();
    if (/\sm$/i.test(t)) t = t.replace(/\sm$/i, '').trim();
    [' vas\u00fat\u00e1llom\u00e1s', ' p\u00e1lyaudvar', ' pu.', ' v\u00e1.', ' va.', ' pu'].forEach((sfx) => {
      if (t.endsWith(sfx)) t = t.slice(0, -sfx.length).trim();
    });
    t = t.replace(/[,.;:\s]+$/g, '').trim();
    return t;
  },
  nameEq(a, b) {
    if (!a || !b) return false;
    if (a === b) return true;
    const na = BkkLib.norm(a);
    const nb = BkkLib.norm(b);
    if (na && nb && na === nb) return true;
    const sa = BkkLib.stationKey(a);
    const sb = BkkLib.stationKey(b);
    if (sa && sb && sa === sb) return true;
    const fa = BkkLib.fold(sa || a);
    const fb = BkkLib.fold(sb || b);
    if (fa && fb && fa === fb) return true;
    const pa = BkkLib.placeNameKey({ name: a });
    const pb = BkkLib.placeNameKey({ name: b });
    return !!(pa && pb && pa === pb);
  },
  fold(s) {
    return BkkLib.norm(s)
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  },
  foldTokens(s) {
    const stop = /^(a|az|es|palyaudvar|vasutallomas|allomas|pu|megallo|ter|utca|ut|varos|budapest)$/;
    return BkkLib.fold(s).split(' ').filter((tok) => tok.length >= 4 && !stop.test(tok));
  },
  foldTokenMatch(a, b) {
    const left = BkkLib.foldTokens(a);
    const right = BkkLib.foldTokens(b);
    if (!left.length || !right.length) return false;
    return left.some((x) => right.some((y) => x === y || x.indexOf(y) >= 0 || y.indexOf(x) >= 0));
  },
  isRailHubName(name) {
    const raw = String(name || '');
    if (!raw) return false;
    if (/aut[o\u00f3]busz/i.test(raw) || /vol[a\u00e1]n/i.test(raw)) return false;
    return /p\u00e1lyaudvar|vas\u00fat\u00e1llom\u00e1s|\bpu\.?\b/i.test(raw);
  },
  isStopArea(stop) {
    if (!stop) return false;
    if (stop.locationType === 1) return true;
    return /_CS/i.test(String(stop.id || ''));
  },
  groupStops(stops) {
    const byName = new Map();
    (stops || []).forEach((s) => {
      if (!s || !s.id || !s.name) return;
      const key = BkkLib.stationKey(s.name) || BkkLib.norm(s.name);
      if (!key) return;
      if (!byName.has(key)) byName.set(key, []);
      byName.get(key).push(s);
    });
    const hits = [];
    byName.forEach((group) => {
      const bkkArea = group.find((s) => /^BKK_CS/i.test(String(s.id || '')));
      const mavParent = group.find((s) => /^BKK_0055[0-9]+$/.test(String(s.id || '')));
      const volanArea = group.find((s) => (
        /^AREA_CS/i.test(String(s.id || '')) ||
        (/^volan_/i.test(String(s.id || '')) && s.locationType === 1)
      ));
      const area = bkkArea || mavParent || volanArea
        || group.find((s) => BkkLib.isStopArea(s) && !/^STOP_/i.test(String(s.id || '')));
      const withParent = group.find((s) => s.parentStationId);
      const named = group.find((s) => !BkkLib.isMetroSuffixName(s.name)) || group[0];
      const pick = area || withParent || named;
      const hit = {
        id: area ? area.id : (pick.parentStationId || pick.id),
        name: named.name,
      };
      const withCoord = group.find((s) => BkkLib.finiteLatLon(s.lat, s.lon)) || pick;
      if (BkkLib.finiteLatLon(withCoord.lat, withCoord.lon)) {
        hit.lat = withCoord.lat;
        hit.lon = withCoord.lon;
      }
      hits.push(hit);
    });
    return hits;
  },
  placeRestKey(rest) {
    const t = String(rest || '').trim();
    if (!t) return '';
    const sk = BkkLib.stationKey(t) || BkkLib.norm(t);
    const folded = BkkLib.fold(sk);
    if (!folded) return '';
    if (/^(vasutallomas|palyaudvar|pu|va)$/.test(folded.replace(/[\s.]/g, ''))) return '';
    return sk;
  },
  splitCityPrefix(name) {
    let t = String(name || '').replace(/[\u2013\u2014]/g, '-').trim();
    t = t.replace(/\s*\(([^)]+)\)\s*$/, '').trim();
    let m = t.match(/^([^,]+),\s*(.+)$/);
    if (m && BkkLib.placeRestKey(m[2])) {
      return { city: m[1].trim(), rest: m[2].trim() };
    }
    m = t.match(/^([^\s,]+)-(.+)$/);
    if (m && BkkLib.placeRestKey(m[2]) && BkkLib.fold(m[1]) === 'budapest') {
      return { city: m[1].trim(), rest: m[2].trim() };
    }
    return null;
  },
  placeNameKey(stop) {
    const raw = String((stop && (stop.name || stop.label)) || '');
    let t = raw.replace(/[\u2013\u2014]/g, '-').trim();
    t = t.replace(/\s*\(([^)]+)\)\s*$/, '').trim();
    t = t.replace(/\s+[ivxlcdm]+\.?\s*ker(?:ület|ulet)\s*$/i, '').trim();
    let key = BkkLib.stationKey(t) || BkkLib.norm(t);
    const split = BkkLib.splitCityPrefix(t);
    if (split) key = BkkLib.placeRestKey(split.rest) || key;
    let folded = BkkLib.fold(key);
    const cityLead = folded.match(/^budapest\s+(.+)$/);
    if (cityLead) folded = cityLead[1];
    folded = folded.replace(/\s+(?:train|railway)\s+station$/, '').trim();
    folded = folded.replace(/\s+(?:p\s*r|parkolo)$/, '').trim();
    if (/^(?:(?:auto)?busz?(?:\s+|-)(?:station|allomas|palyaudvar)|aut\s*all)$/.test(folded)) {
      folded = 'aut all';
    } else {
      folded = folded.replace(
        /\s+(?:(?:auto)?busz?(?:\s+|-)(?:station|allomas|palyaudvar)|aut\s*all)$/,
        ' aut all',
      );
    }
    return folded;
  },
  stopCityKey(stop) {
    const name = String((stop && stop.name) || '');
    const split = BkkLib.splitCityPrefix(name);
    if (split) return BkkLib.fold(split.city);
    const label = String((stop && (stop.label || stop.name)) || '');
    const paren = label.match(/\(([^)]+)\)\s*$/);
    if (paren && !/^(m[\u00e1a]v|vol[\u00e1a]n)$/i.test(paren[1])) {
      return BkkLib.fold(paren[1]);
    }
    return '';
  },
  plannerStopKey(stop) {
    return BkkLib.stopCityKey(stop) + '|' + BkkLib.placeNameKey(stop);
  },
  stopIdRank(id) {
    const s = String(id || '');
    if (/^BKK_CS/i.test(s)) return 1;
    if (/^BKK_0055\d{6,}$/i.test(s)) return 3;
    if (/^BKK_/i.test(s) && s.split('_').length <= 2) return 2;
    if (/^BKK_/i.test(s)) return 4;
    if (/^hu-bkk_/i.test(s)) return 5;
    if (/^(?:volan_|hkir_|AREA_CS|hu-volanbusz_)/i.test(s)) return 6;
    if (/^[a-z][a-z0-9]*:/i.test(s) && s.indexOf('/') < 0) return 7;
    if (/^hu-/i.test(s)) return 8;
    if (/^(?:geo:|node\/|way\/)/i.test(s)) return 10;
    if (/^(?:cz-|sk-|ua-|de-|al-|at-|eu-|pl-)/i.test(s)) return 12;
    if (/^STOP_/i.test(s)) return 20;
    return 9;
  },
  stopIdFamily(stop) {
    if (BkkLib.isPlaceStop(stop)) return 'osm';
    const s = String((stop && stop.id) || '');
    if (/^(?:geo:|node\/|way\/)/i.test(s)) return 'osm';
    if (/^BKK_0055/i.test(s) || /(?:^hu-mav_|railway)/i.test(s)) return 'mav';
    if (/^(?:volan_|hkir_|AREA_CS|hu-volanbusz_)/i.test(s)) return 'volan';
    if (/^(?:BKK_|hu-bkk_)/i.test(s)) return 'bkk';
    if (/^[a-z][a-z0-9]*:/i.test(s) && s.indexOf('/') < 0) return 'city';
    if (/^hu-/i.test(s)) return 'hu';
    return 'other';
  },
  stopsTooFar(a, b) {
    const pa = BkkLib.finiteLatLon(a && a.lat, a && a.lon);
    const pb = BkkLib.finiteLatLon(b && b.lat, b && b.lon);
    if (!pa || !pb) return false;
    return BkkLib.haversineMeters(pa, pb) > 400;
  },
  stopsCloseEnough(a, b) {
    const pa = BkkLib.finiteLatLon(a && a.lat, a && a.lon);
    const pb = BkkLib.finiteLatLon(b && b.lat, b && b.lon);
    if (!pa || !pb) return true;
    return BkkLib.haversineMeters(pa, pb) <= 300;
  },
  citiesConflict(a, b) {
    const ca = BkkLib.stopCityKey(a);
    const cb = BkkLib.stopCityKey(b);
    return !!(ca && cb && ca !== cb);
  },
  splitStopClusters(group) {
    const clusters = [];
    (group || []).forEach((s) => {
      let joined = null;
      for (let i = 0; i < clusters.length; i++) {
        const cl = clusters[i];
        if (cl.some((x) => BkkLib.citiesConflict(x, s))) continue;
        const mixPlace = cl.some((x) => BkkLib.isPlaceStop(x) !== BkkLib.isPlaceStop(s));
        const mixOp = cl.some((x) => BkkLib.stopIdFamily(x) !== BkkLib.stopIdFamily(s));
        if (!mixPlace && !mixOp && cl.some((x) => BkkLib.stopsTooFar(x, s))) continue;
        joined = cl;
        break;
      }
      if (joined) joined.push(s);
      else clusters.push([s]);
    });
    return clusters;
  },
  pickCollapsedStop(cluster) {
    const list = (cluster || []).filter(Boolean);
    if (!list.length) return null;
    const ranked = list.slice().sort((a, b) => {
      const ra = BkkLib.stopIdRank(a.id);
      const rb = BkkLib.stopIdRank(b.id);
      if (ra !== rb) return ra - rb;
      const ac = BkkLib.finiteLatLon(a.lat, a.lon) ? 0 : 1;
      const bc = BkkLib.finiteLatLon(b.lat, b.lon) ? 0 : 1;
      return ac - bc;
    });
    const win = Object.assign({}, ranked[0]);
    list.forEach((s) => {
      if (!BkkLib.finiteLatLon(win.lat, win.lon) && BkkLib.finiteLatLon(s.lat, s.lon)) {
        win.lat = s.lat;
        win.lon = s.lon;
      }
      if (!win.kind && s.kind && s.kind !== 'place' && s.kind !== 'address') win.kind = s.kind;
    });
    const named = list.find((s) => (
      s.name && !BkkLib.isPlaceStop(s) && !BkkLib.isMetroSuffixName(s.name)
    )) || list.find((s) => s.name && !BkkLib.isPlaceStop(s)) || win;
    if (named && named.name) win.name = named.name;
    if (win.label) {
      win.label = String(win.label).replace(/\s*\((?:M[\u00c1A]V|Vol[\u00e1a]n)\)\s*$/i, '');
    }
    return win;
  },
  collapseSameNameStops(stops) {
    const byKey = new Map();
    (stops || []).forEach((s) => {
      if (!s || !s.id || !s.name) return;
      const key = BkkLib.placeNameKey(s);
      if (!key) return;
      if (!byKey.has(key)) byKey.set(key, []);
      byKey.get(key).push(s);
    });
    const out = [];
    byKey.forEach((group) => {
      BkkLib.splitStopClusters(group).forEach((cluster) => {
        const hit = BkkLib.pickCollapsedStop(cluster);
        if (hit) out.push(hit);
      });
    });
    if (out.some((s) => !BkkLib.isPlaceStop(s))) {
      return out.filter((s) => !BkkLib.isPlaceStop(s));
    }
    return out;
  },
  destHitsForQuery(dests, q, extras) {
    const f = BkkLib.fold(q || '');
    const out = [];
    const seen = new Map();
    const add = (d) => {
      if (!d || !d.name) return;
      const id = d.id || d.stopId || '';
      let key = BkkLib.placeNameKey(d) || BkkLib.stationKey(d.name) || BkkLib.norm(d.name);
      if (id && /^geo:/i.test(id) && String(d.kind || '') === 'address') key = key + '|' + id;
      if (!key) return;
      const prev = seen.get(key);
      if (prev) {
        if (BkkLib.stopIdRank(id) < BkkLib.stopIdRank(prev.id)) prev.id = id;
        if (BkkLib.elviraStationCode(id) && !BkkLib.elviraStationCode(prev.id)) prev.id = id;
        if (/^geo:/i.test(String(prev.id || '')) && id && !/^geo:/i.test(id)) prev.id = id;
        if (Number.isFinite(Number(d.lat)) && !Number.isFinite(Number(prev.lat))) {
          prev.lat = Number(d.lat);
          prev.lon = Number(d.lon);
        }
        if (d.kind && !prev.kind) prev.kind = d.kind;
        return;
      }
      const row = { key: key, name: d.name, id: id };
      if (Number.isFinite(Number(d.lat))) row.lat = Number(d.lat);
      if (Number.isFinite(Number(d.lon))) row.lon = Number(d.lon);
      if (d.kind) row.kind = d.kind;
      if (d.label) row.label = d.label;
      seen.set(key, row);
      out.push(row);
    };
    (extras || []).forEach(add);
    (dests || []).forEach((d) => {
      if (f) {
        const dn = BkkLib.fold(d.name);
        if (dn.indexOf(f) < 0 && f.indexOf(dn) < 0) return;
      }
      add(d);
    });
    return out;
  },
  isMavRoute(rt) {
    const t = String((rt && rt.type) || '').toUpperCase();
    if (t.indexOf('SUBURBAN') >= 0) return false;
    return t === 'RAIL' || t === 'RAILWAY' || t === 'TRAIN';
  },
  isVolanRoute(rt) {
    if (!rt) return false;
    if (String(rt.type || '').toUpperCase() === 'COACH') return true;
    if (/^volan_/i.test(String(rt.id || ''))) return true;
    return /volan/i.test(String(rt.agencyId || ''));
  },
  isMavStop(stop) {
    if (!stop) return false;
    const t = String(stop.type || '').toUpperCase();
    if (t.indexOf('SUBURBAN') >= 0) return false;
    if (t === 'RAIL' || t === 'RAILWAY' || t === 'TRAIN') return true;
    return /^BKK_0055/.test(String(stop.id || ''));
  },
  isVolanStop(stop) {
    if (!stop) return false;
    const id = String(stop.id || '');
    if (/^volan_/i.test(id) || /^AREA_CS/i.test(id) || /^hkir_/i.test(id)) return true;
    if (/^hu-volanbusz_/i.test(id)) return true;
    if (String(stop.type || '').toUpperCase() === 'COACH') return true;
    return false;
  },
  /* BKK street stops next to a metro are named "… M". The hop card and
     commute sensors use the non-M stop (the metro / parent name). */
  isMetroSuffixName(name) {
    const n = String(name || '').normalize('NFC').replace(/\s+/g, ' ').trim();
    return /\sM$/i.test(n);
  },
  isBkkStop(stop) {
    if (!stop || BkkLib.isMavStop(stop) || BkkLib.isVolanStop(stop)) return false;
    const id = String(stop.id || '');
    if (/^AREA_/i.test(id) || /^STOP_/i.test(id)) return false;
    const t = String(stop.type || '').toUpperCase();
    if (t === 'COACH') return false;
    if (t === 'BUS' || t === 'TRAM' || t === 'SUBWAY' || t === 'TROLLEYBUS' ||
        t === 'FERRY' || t.indexOf('SUBURBAN') >= 0) return true;
    if (!/^BKK_/i.test(id)) return false;
    if (stop.locationType === 1) return true;
    return !t;
  },
  mode(cfg) {
    if (cfg && cfg.volan) return 'volan';
    if (cfg && cfg.helyi) return 'helyi';
    if (cfg && cfg.mav) return 'mav';
    return 'bkk';
  },
  routeMatchesMode(rt, mode) {
    if (mode === 'mav') return BkkLib.isMavRoute(rt);
    if (mode === 'volan') return BkkLib.isVolanRoute(rt);
    if (mode === 'all') return true;
    return !BkkLib.isMavRoute(rt) && !BkkLib.isVolanRoute(rt);
  },
  stopNum(id) {
    const s = String(id || '');
    const m = s.match(/^(?:volan_|AREA_CS|hkir_)(\d+)/i);
    if (m) return m[1];
    const n = s.match(/(\d{5,})/);
    return n ? n[1] : '';
  },
  elviraStationCode(id) {
    const m = String(id || '').match(/(\d{9})/);
    return m ? m[1] : '';
  },
  /* BKK street/metro parents (CSF…) sit next to MÁV stations but have no
     9-digit ELVIRA code. Map the well-known names; searchStops fills the rest. */
  elviraCodeFromName(name) {
    const f = BkkLib.fold(name);
    if (f === 'keleti' || f === 'budapest keleti') return '005510017';
    if (f === 'kelenfold' || f === 'budapest kelenfold') return '005501024';
    if (f === 'szekesfehervar' || f === 'szechenyi ut szekesfehervar') return '005503269';
    return '';
  },
  isRailDest(dest) {
    if (!dest) return false;
    if (BkkLib.isMavStop(dest)) return true;
    if (BkkLib.elviraStationCode(dest.id)) return true;
    if (BkkLib.elviraCodeFromName(dest.name)) return true;
    return BkkLib.isRailHubName(dest.name);
  },
  async elviraResolveCode(apiKey, id, name) {
    const direct = BkkLib.elviraStationCode(id);
    if (direct) return direct;
    const mapped = BkkLib.elviraCodeFromName(name);
    if (mapped) return mapped;
    const n = String(name || '').trim();
    if (!BkkLib.hasFutar(apiKey) || !n) return '';
    const queries = [n];
    const key = BkkLib.stationKey(n);
    if (key && key !== n) queries.push(key);
    BkkLib.aliasQueries(n).forEach((q) => queries.push(q));
    const seen = new Set();
    for (let i = 0; i < queries.length; i++) {
      const q = queries[i];
      if (!q || seen.has(q)) continue;
      seen.add(q);
      try {
        const hits = await BkkLib.searchStops(apiKey, q, 'mav');
        for (let j = 0; j < hits.length; j++) {
          const c = BkkLib.elviraStationCode(hits[j].id);
          if (c) {
            return c;
          }
        }
      } catch (_e) { /* search is a fallback */ }
    }
    return '';
  },
  sameStop(id, originId) {
    if (!id || !originId) return false;
    if (id === originId) return true;
    const a = String(id);
    const b = String(originId);
    const va = BkkLib.stopNum(a);
    const vb = BkkLib.stopNum(b);
    if (va && vb && (/^(?:volan_|AREA_CS|hkir_)/i.test(a) || /^(?:volan_|AREA_CS|hkir_)/i.test(b))) {
      return va === vb;
    }
    const base = (s) => s.replace(/^BKK_/, '').split('_')[0];
    if (/^volan_/i.test(a) || /^volan_/i.test(b) || /^AREA_/i.test(a) || /^AREA_/i.test(b) || /^hkir_/i.test(a) || /^hkir_/i.test(b)) {
      return false;
    }
    return base(a) === base(b);
  },
  aliasQueries(q) {
    const f = BkkLib.fold(q);
    const extra = [];
    if (f.includes('arpad hid')) extra.push('Goncz Arpad varoskozpont');
    if (f.includes('moszkva')) extra.push('Szell Kalman ter');
    if (f === 'keleti' || f.indexOf('keleti ') === 0) extra.push('Budapest-Keleti');
    if (f === 'kelenfold' || f.indexOf('kelenfold ') === 0) extra.push('Budapest-Kelenfold');
    const key = BkkLib.stationKey(q);
    if (key && key !== q && extra.indexOf(key) < 0) extra.push(key);
    return extra;
  },
  labelAliased(q, hit) {
    const f = BkkLib.fold(q);
    const n = BkkLib.fold(hit.name);
    if (f.includes('arpad hid') && n === 'goncz arpad varoskozpont') {
      return 'G\u00f6ncz \u00c1rp\u00e1d v\u00e1rosk\u00f6zpont (M3, \u00c1rp\u00e1d h\u00edd)';
    }
    return hit.name;
  },
});
