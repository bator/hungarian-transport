/* Hungarian transport cards. Generated from src/ by 'npm run build'; do not edit. */

// src/lib/core.js
var BkkLib = {};

// src/lib/names.js
Object.assign(BkkLib, {
  esc(s) {
    return String(s === null || s === void 0 ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  },
  norm(name) {
    let t2 = String(name || "").normalize("NFC");
    t2 = t2.replace(/[\u200B-\u200D\uFEFF]/g, "");
    t2 = t2.replace(/[\u00A0\u202F]/g, " ");
    t2 = t2.trim().toLowerCase();
    t2 = t2.replace(/[\u2013\u2014]/g, "-");
    t2 = t2.replace(/\s+/g, " ");
    [" vasútállomás", " pályaudvar", " pu."].forEach((sfx) => {
      if (t2.endsWith(sfx)) t2 = t2.slice(0, -sfx.length).trim();
    });
    t2 = t2.replace(/[,.;:\s]+$/g, "").trim();
    return t2;
  },
  /* "Keleti pályaudvar M" (street) and "Keleti pályaudvar" (metro) are one place. */
  stationKey(name) {
    let t2 = String(name || "").normalize("NFC");
    t2 = t2.replace(/[\u200B-\u200D\uFEFF]/g, "");
    t2 = t2.replace(/[\u00A0\u202F]/g, " ");
    t2 = t2.trim().toLowerCase();
    t2 = t2.replace(/[\u2013\u2014]/g, "-");
    t2 = t2.replace(/\s+/g, " ");
    t2 = t2.replace(/\s+m\+h$/i, "").trim();
    t2 = t2.replace(/\s+m\+$/i, "").trim();
    if (/\sm$/i.test(t2)) t2 = t2.replace(/\sm$/i, "").trim();
    [" vasútállomás", " pályaudvar", " pu.", " vá.", " va.", " pu"].forEach((sfx) => {
      if (t2.endsWith(sfx)) t2 = t2.slice(0, -sfx.length).trim();
    });
    t2 = t2.replace(/[,.;:\s]+$/g, "").trim();
    return t2;
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
    if (!pa || !pb || pa !== pb) return false;
    return !BkkLib.citiesConflict({ name: a }, { name: b });
  },
  fold(s) {
    return BkkLib.norm(s).normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
  },
  foldTokens(s) {
    const stop = /^(a|az|es|palyaudvar|vasutallomas|allomas|pu|megallo|ter|utca|ut|varos|budapest)$/;
    return BkkLib.fold(s).split(" ").filter((tok) => tok.length >= 4 && !stop.test(tok));
  },
  foldTokenMatch(a, b) {
    const left = BkkLib.foldTokens(a);
    const right = BkkLib.foldTokens(b);
    if (!left.length || !right.length) return false;
    return left.some((x) => right.some((y) => x === y || x.indexOf(y) >= 0 || y.indexOf(x) >= 0));
  },
  isRailHubName(name) {
    const raw = String(name || "");
    if (!raw) return false;
    if (/busz/i.test(raw) || /vol[a\u00e1]n/i.test(raw)) return false;
    return /p\u00e1lyaudvar|vas\u00fat\u00e1llom\u00e1s|\bpu\.?\b/i.test(raw);
  },
  isStopArea(stop) {
    if (!stop) return false;
    if (stop.locationType === 1) return true;
    return /_CS/i.test(String(stop.id || ""));
  },
  groupStops(stops) {
    const byName = /* @__PURE__ */ new Map();
    (stops || []).forEach((s) => {
      if (!s || !s.id || !s.name) return;
      const key = BkkLib.stationKey(s.name) || BkkLib.norm(s.name);
      if (!key) return;
      if (!byName.has(key)) byName.set(key, []);
      byName.get(key).push(s);
    });
    const hits = [];
    byName.forEach((group) => {
      const bkkArea = group.find((s) => /^BKK_CS/i.test(String(s.id || "")));
      const mavParent = group.find((s) => /^BKK_0055[0-9]+$/.test(String(s.id || "")));
      const volanArea = group.find((s) => /^AREA_CS/i.test(String(s.id || "")) || /^volan_/i.test(String(s.id || "")) && s.locationType === 1);
      const area = bkkArea || mavParent || volanArea || group.find((s) => BkkLib.isStopArea(s) && !/^STOP_/i.test(String(s.id || "")));
      const withParent = group.find((s) => s.parentStationId);
      const named = group.find((s) => !BkkLib.isMetroSuffixName(s.name)) || group[0];
      const pick = area || withParent || named;
      const hit = {
        id: area ? area.id : pick.parentStationId || pick.id,
        name: named.name
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
    const t2 = String(rest || "").trim();
    if (!t2) return "";
    const sk = BkkLib.stationKey(t2) || BkkLib.norm(t2);
    const folded = BkkLib.fold(sk);
    if (!folded) return "";
    if (/^(vasutallomas|palyaudvar|pu|va)$/.test(folded.replace(/[\s.]/g, ""))) return "";
    return sk;
  },
  splitCityPrefix(name) {
    let t2 = String(name || "").replace(/[\u2013\u2014]/g, "-").trim();
    t2 = t2.replace(/\s*\(([^)]+)\)\s*$/, "").trim();
    let m = t2.match(/^([^,]+),\s*(.+)$/);
    if (m && BkkLib.placeRestKey(m[2])) {
      return { city: m[1].trim(), rest: m[2].trim() };
    }
    m = t2.match(/^([^\s,]+)-(.+)$/);
    if (m && BkkLib.placeRestKey(m[2]) && BkkLib.fold(m[1]) === "budapest") {
      return { city: m[1].trim(), rest: m[2].trim() };
    }
    return null;
  },
  placeNameKey(stop) {
    const raw = String(stop && (stop.name || stop.label) || "");
    let t2 = raw.replace(/[\u2013\u2014]/g, "-").trim();
    t2 = t2.replace(/\s*\(([^)]+)\)\s*$/, "").trim();
    t2 = t2.replace(/\s+[ivxlcdm]+\.?\s*ker(?:ület|ulet)\s*$/i, "").trim();
    let key = BkkLib.stationKey(t2) || BkkLib.norm(t2);
    const split = BkkLib.splitCityPrefix(t2);
    if (split) key = BkkLib.placeRestKey(split.rest) || key;
    let folded = BkkLib.fold(key);
    const cityLead = folded.match(/^budapest\s+(.+)$/);
    if (cityLead) folded = cityLead[1];
    folded = folded.replace(/\s+(?:train|railway)\s+station$/, "").trim();
    folded = folded.replace(/\s+(?:p\s*r|parkolo)$/, "").trim();
    if (/^(?:(?:auto)?busz?(?:\s+|-)(?:station|allomas|palyaudvar)|aut\s*all)$/.test(folded)) {
      folded = "aut all";
    } else {
      folded = folded.replace(
        /\s+(?:(?:auto)?busz?(?:\s+|-)(?:station|allomas|palyaudvar)|aut\s*all)$/,
        " aut all"
      );
    }
    return folded;
  },
  stopCityKey(stop) {
    const name = String(stop && stop.name || "");
    const split = BkkLib.splitCityPrefix(name);
    if (split) return BkkLib.fold(split.city);
    const label = String(stop && (stop.label || stop.name) || "");
    const paren = label.match(/\(([^)]+)\)\s*$/);
    if (paren && !/^(m[\u00e1a]v|vol[\u00e1a]n)$/i.test(paren[1])) {
      return BkkLib.fold(paren[1]);
    }
    return "";
  },
  plannerStopKey(stop) {
    return BkkLib.stopCityKey(stop) + "|" + BkkLib.placeNameKey(stop);
  },
  stopIdRank(id) {
    const s = String(id || "");
    if (/^BKK_CS/i.test(s)) return 1;
    if (/^BKK_0055\d{6,}$/i.test(s)) return 3;
    if (/^BKK_/i.test(s) && s.split("_").length <= 2) return 2;
    if (/^BKK_/i.test(s)) return 4;
    if (/^hu-bkk_/i.test(s)) return 5;
    if (/^(?:volan_|hkir_|AREA_CS|hu-volanbusz_)/i.test(s)) return 6;
    if (/^[a-z][a-z0-9-]*:/i.test(s) && s.indexOf("/") < 0) return 7;
    if (/^hu-/i.test(s)) return 8;
    if (/^(?:geo:|node\/|way\/)/i.test(s)) return 10;
    if (/^(?:cz-|sk-|ua-|de-|al-|at-|eu-|pl-)/i.test(s)) return 12;
    if (/^STOP_/i.test(s)) return 20;
    return 9;
  },
  stopIdFamily(stop) {
    if (BkkLib.isPlaceStop(stop)) return "osm";
    const s = String(stop && stop.id || "");
    if (/^(?:geo:|node\/|way\/)/i.test(s)) return "osm";
    if (/^BKK_0055/i.test(s) || /(?:^hu-mav_|railway)/i.test(s)) return "mav";
    if (/^(?:volan_|hkir_|AREA_CS|hu-volanbusz_)/i.test(s)) return "volan";
    if (/^(?:BKK_|hu-bkk_)/i.test(s)) return "bkk";
    if (/^[a-z][a-z0-9-]*:/i.test(s) && s.indexOf("/") < 0) return "city";
    if (/^hu-/i.test(s)) return "hu";
    return "other";
  },
  stopsTooFar(a, b, maxM) {
    const pa = BkkLib.finiteLatLon(a && a.lat, a && a.lon);
    const pb = BkkLib.finiteLatLon(b && b.lat, b && b.lon);
    if (!pa || !pb) return false;
    return BkkLib.haversineMeters(pa, pb) > (maxM || 400);
  },
  stopsCloseEnough(a, b) {
    const pa = BkkLib.finiteLatLon(a && a.lat, a && a.lon);
    const pb = BkkLib.finiteLatLon(b && b.lat, b && b.lon);
    if (!pa || !pb) return true;
    return BkkLib.haversineMeters(pa, pb) <= 300;
  },
  /* Two named towns, or two cross streets ("Szeged, Londoni körút (Bakay
     Nándor utca)" and "… (Kálvária sugárút)"), are two places. */
  citiesConflict(a, b) {
    const ca = BkkLib.stopCityKey(a);
    const cb = BkkLib.stopCityKey(b);
    if (ca && cb && ca !== cb) return true;
    const qa = BkkLib.stopQualifier(a);
    const qb = BkkLib.stopQualifier(b);
    return !!(qa && qb && qa !== qb);
  },
  /* Same-named stops of one feed are one stop within 400 m. A stop and a
     place, or two feeds' stops, are one place in the same town (a foreign
     coach feed puts Kelenföld at Népliget), else within a kilometre: the
     town of Budapest's "Fő tér" is not Tatabánya's. */
  splitStopClusters(group) {
    const clusters = [];
    const townsConflict = (x, s) => {
      const tx = BkkLib.stopTownKey(x);
      const ts = BkkLib.stopTownKey(s);
      return !!(tx && ts && tx !== ts);
    };
    const sameTown = (x, s) => {
      const tx = BkkLib.stopTownKey(x);
      return !!tx && tx === BkkLib.stopTownKey(s);
    };
    (group || []).forEach((s) => {
      let joined = null;
      for (let i = 0; i < clusters.length; i++) {
        const cl = clusters[i];
        if (cl.some((x) => BkkLib.citiesConflict(x, s) || townsConflict(x, s))) continue;
        const mixPlace = cl.some((x) => BkkLib.isPlaceStop(x) !== BkkLib.isPlaceStop(s));
        const mixOp = cl.some((x) => BkkLib.stopIdFamily(x) !== BkkLib.stopIdFamily(s));
        const far = mixPlace || mixOp ? cl.some((x) => !sameTown(x, s) && BkkLib.stopsTooFar(x, s, 1e3)) : cl.some((x) => BkkLib.stopsTooFar(x, s));
        if (far) continue;
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
      const pa = BkkLib.isPlaceStop(a) ? 1 : 0;
      const pb = BkkLib.isPlaceStop(b) ? 1 : 0;
      if (pa !== pb) return pa - pb;
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
      if (!win.kind && s.kind && s.kind !== "place" && s.kind !== "address") win.kind = s.kind;
    });
    const named = list.find((s) => s.name && !BkkLib.isPlaceStop(s) && !BkkLib.isMetroSuffixName(s.name)) || list.find((s) => s.name && !BkkLib.isPlaceStop(s)) || win;
    if (named && named.name) win.name = named.name;
    if (win.label) {
      win.label = String(win.label).replace(/\s*\((?:M[\u00c1A]V|Vol[\u00e1a]n)\)\s*$/i, "");
    }
    return win;
  },
  collapseSameNameStops(stops) {
    const byKey = /* @__PURE__ */ new Map();
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
    const real = out.filter((s) => !BkkLib.isPlaceStop(s));
    if (!real.length) return out;
    return out.filter((s) => {
      if (!BkkLib.isPlaceStop(s) || String(s.kind || "") === "address") return true;
      const town = BkkLib.stopTownKey(s);
      return !!town && real.every((x) => {
        const other = BkkLib.stopTownKey(x);
        return !!other && other !== town;
      });
    });
  },
  /* The town a picker row stands in: its "Town, " prefix, the town of its
     feed (a city feed's area), the town our own label adds ("Fő tér
     (Tatabánya)"), Budapest for a BKK stop; '' when nothing tells. A
     bracket in the stop's own name is a cross street (Szeged's "Londoni
     körút (Bakay Nándor utca)"), not a town. */
  stopTownKey(stop) {
    const name = String(stop && stop.name || "");
    const split = BkkLib.splitCityPrefix(name);
    if (split) return BkkLib.fold(split.city);
    if (stop && stop.area) return BkkLib.fold(stop.area);
    if (BkkLib.stopIdFamily(stop) === "city" && !BkkLib.isPlaceStop(stop)) {
      return BkkLib.fold(String(stop.id).split(":")[0].replace(/^volan-/i, "").replace(/-/g, " "));
    }
    const label = String(stop && stop.label || "");
    if (label && label !== name && label.indexOf(name) === 0) {
      const paren = label.slice(name.length).match(/^\s*\(([^)]+)\)\s*$/);
      if (paren && !/^(m[\u00e1a]v|vol[\u00e1a]n)$/i.test(paren[1])) return BkkLib.fold(paren[1]);
    }
    return BkkLib.stopIdFamily(stop) === "bkk" ? "budapest" : "";
  },
  /* The cross street in brackets at the end of a stop's own name. */
  stopQualifier(stop) {
    const m = String(stop && stop.name || "").match(/\(([^)]+)\)\s*$/);
    if (!m || /^(m[\u00e1a]v|vol[\u00e1a]n)$/i.test(m[1])) return "";
    return BkkLib.fold(m[1]);
  },
  /* One row per place: the same stop from FUTÁR, Volán and the search is
     merged, but the town stays part of the place (116 towns have an
     "autóbusz-állomás"): rows of two known towns, two cross streets, or
     more than a kilometre apart stay apart, and a row never takes another
     row's town. `key` is the reachable-list key when there is one, so it
     finds that destination's routes; `routeKeys` holds every merged
     source's key. */
  destHitsForQuery(dests, q, extras) {
    const f = BkkLib.fold(q || "");
    const out = [];
    const seen = /* @__PURE__ */ new Map();
    const add = (d) => {
      if (!d || !d.name) return;
      const id = d.id || d.stopId || "";
      let place = BkkLib.placeNameKey(d) || BkkLib.stationKey(d.name) || BkkLib.norm(d.name);
      if (!place) return;
      if (id && /^geo:/i.test(id) && String(d.kind || "") === "address") place = place + "|" + id;
      const town = BkkLib.stopTownKey(d);
      const qual = BkkLib.stopQualifier(d);
      const fits = (r) => !(r.town && town && r.town !== town) && !(r.qual && qual && r.qual !== qual) && !BkkLib.stopsTooFar(r, d, 1e3);
      const rows = seen.get(place) || [];
      const gap = (r) => {
        const pa = BkkLib.finiteLatLon(r.lat, r.lon);
        const pb = BkkLib.finiteLatLon(d.lat, d.lon);
        return pa && pb ? BkkLib.haversineMeters(pa, pb) : Infinity;
      };
      const prev = rows.find((r) => r.town === town && r.qual === qual && fits(r)) || rows.filter(fits).sort((x, y) => gap(x) - gap(y))[0];
      if (prev) {
        if (d.key && prev.routeKeys.indexOf(d.key) < 0) {
          prev.routeKeys.push(d.key);
          if (!prev.fromList) {
            prev.key = d.key;
            prev.fromList = true;
          }
        }
        if (BkkLib.stopIdRank(id) < BkkLib.stopIdRank(prev.id)) prev.id = id;
        if (BkkLib.elviraStationCode(id) && !BkkLib.elviraStationCode(prev.id)) prev.id = id;
        if (/^geo:/i.test(String(prev.id || "")) && id && !/^geo:/i.test(id)) prev.id = id;
        if (BkkLib.finiteLatLon(d.lat, d.lon) && !BkkLib.finiteLatLon(prev.lat, prev.lon)) {
          prev.lat = Number(d.lat);
          prev.lon = Number(d.lon);
        }
        if (d.kind && !prev.kind) prev.kind = d.kind;
        return;
      }
      const row = {
        key: d.key || place,
        name: d.name,
        id,
        routeKeys: d.key ? [d.key] : [],
        fromList: !!d.key
      };
      if (BkkLib.finiteLatLon(d.lat, d.lon)) {
        row.lat = Number(d.lat);
        row.lon = Number(d.lon);
      }
      if (d.kind) row.kind = d.kind;
      if (d.label) row.label = d.label;
      if (d.area) row.area = d.area;
      Object.defineProperty(row, "town", { value: town, enumerable: false });
      Object.defineProperty(row, "qual", { value: qual, enumerable: false });
      rows.push(row);
      seen.set(place, rows);
      out.push(row);
    };
    (extras || []).forEach(add);
    (dests || []).forEach((d) => {
      if (f) {
        const dn = BkkLib.fold(d.name);
        if (dn.indexOf(f) < 0 && (" " + f + " ").indexOf(" " + dn + " ") < 0) return;
      }
      add(d);
    });
    return out;
  },
  isMavRoute(rt) {
    const t2 = String(rt && rt.type || "").toUpperCase();
    if (t2.indexOf("SUBURBAN") >= 0) return false;
    return t2 === "RAIL" || t2 === "RAILWAY" || t2 === "TRAIN";
  },
  isVolanRoute(rt) {
    if (!rt) return false;
    if (String(rt.type || "").toUpperCase() === "COACH") return true;
    if (/^volan_/i.test(String(rt.id || ""))) return true;
    return /volan/i.test(String(rt.agencyId || ""));
  },
  isMavStop(stop) {
    if (!stop) return false;
    const t2 = String(stop.type || "").toUpperCase();
    if (t2.indexOf("SUBURBAN") >= 0) return false;
    if (t2 === "RAIL" || t2 === "RAILWAY" || t2 === "TRAIN") return true;
    return /^BKK_0055/.test(String(stop.id || ""));
  },
  isVolanStop(stop) {
    if (!stop) return false;
    const id = String(stop.id || "");
    if (/^volan_/i.test(id) || /^AREA_CS/i.test(id) || /^hkir_/i.test(id)) return true;
    if (/^hu-volanbusz_/i.test(id)) return true;
    if (String(stop.type || "").toUpperCase() === "COACH") return true;
    return false;
  },
  /* BKK street stops next to a metro are named "… M". The hop card and
     commute sensors use the non-M stop (the metro / parent name). */
  isMetroSuffixName(name) {
    const n = String(name || "").normalize("NFC").replace(/\s+/g, " ").trim();
    return /\sM$/i.test(n);
  },
  isBkkStop(stop) {
    if (!stop || BkkLib.isMavStop(stop) || BkkLib.isVolanStop(stop)) return false;
    const id = String(stop.id || "");
    if (/^AREA_/i.test(id) || /^STOP_/i.test(id)) return false;
    const t2 = String(stop.type || "").toUpperCase();
    if (t2 === "COACH") return false;
    if (t2 === "BUS" || t2 === "TRAM" || t2 === "SUBWAY" || t2 === "TROLLEYBUS" || t2 === "FERRY" || t2.indexOf("SUBURBAN") >= 0) return true;
    if (!/^BKK_/i.test(id)) return false;
    if (stop.locationType === 1) return true;
    return !t2;
  },
  mode(cfg) {
    if (cfg && cfg.volan) return "volan";
    if (cfg && cfg.helyi) return "helyi";
    if (cfg && cfg.mav) return "mav";
    return "bkk";
  },
  routeMatchesMode(rt, mode) {
    if (mode === "mav") return BkkLib.isMavRoute(rt);
    if (mode === "volan") return BkkLib.isVolanRoute(rt);
    if (mode === "all") return true;
    return !BkkLib.isMavRoute(rt) && !BkkLib.isVolanRoute(rt);
  },
  stopNum(id) {
    const s = String(id || "");
    const m = s.match(/^(?:volan_|AREA_CS|hkir_)(\d+)/i);
    if (m) return m[1];
    const n = s.match(/(\d{5,})/);
    return n ? n[1] : "";
  },
  /* Only a MÁV station's id carries one (BKK_005511387, BKK_CS…,
     hu-mav_…, the bare code): a city stop's longer number (szeged:2430584836)
     is not a station code. */
  elviraStationCode(id) {
    const m = String(id || "").match(/^(?:BKK_(?:CS)?|hu-mav_)?(\d{9})$/i);
    return m ? m[1] : "";
  },
  /* BKK street/metro parents (CSF…) sit next to MÁV stations but have no
     9-digit ELVIRA code. Map the well-known names; searchStops fills the rest. */
  elviraCodeFromName(name) {
    const f = BkkLib.fold(name);
    if (f === "keleti" || f === "budapest keleti") return "005510017";
    if (f === "kelenfold" || f === "budapest kelenfold") return "005501024";
    if (f === "szekesfehervar" || f === "szechenyi ut szekesfehervar") return "005503269";
    return "";
  },
  isRailDest(dest) {
    if (!dest) return false;
    if (BkkLib.isMavStop(dest)) return true;
    if (BkkLib.elviraStationCode(dest.id)) return true;
    if (BkkLib.elviraCodeFromName(dest.name)) return true;
    return BkkLib.isRailHubName(dest.name);
  },
  /* A stop whose id carries no ELVIRA code is looked up by name, which is a
     few FUTÁR searches: on every poll without this memo. A found code is
     kept for the page, a miss for ten minutes. */
  _elviraCodes: /* @__PURE__ */ new Map(),
  async elviraResolveCode(apiKey, id, name) {
    const direct = BkkLib.elviraStationCode(id);
    if (direct) return direct;
    const mapped = BkkLib.elviraCodeFromName(name);
    if (mapped) return mapped;
    const n = String(name || "").trim();
    if (!BkkLib.hasFutar(apiKey) || !n) return "";
    const memoKey = String(id || "") + "|" + n;
    const hit = BkkLib._elviraCodes.get(memoKey);
    if (hit && (hit.code || Date.now() - hit.ts < 6e5)) return hit.code;
    const code = await this._searchElviraCode(apiKey, n);
    BkkLib._elviraCodes.set(memoKey, { code, ts: Date.now() });
    return code;
  },
  async _searchElviraCode(apiKey, n) {
    const queries = [n];
    const key = BkkLib.stationKey(n);
    if (key && key !== n) queries.push(key);
    BkkLib.aliasQueries(n).forEach((q) => queries.push(q));
    const seen = /* @__PURE__ */ new Set();
    for (let i = 0; i < queries.length; i++) {
      const q = queries[i];
      if (!q || seen.has(q)) continue;
      seen.add(q);
      try {
        const hits = await this.searchStops(apiKey, q, "mav");
        for (let j = 0; j < hits.length; j++) {
          const c = BkkLib.elviraStationCode(hits[j].id);
          if (c) {
            return c;
          }
        }
      } catch (_e) {
      }
    }
    return "";
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
    const base = (s) => s.replace(/^BKK_/, "").split("_")[0];
    if (/^volan_/i.test(a) || /^volan_/i.test(b) || /^AREA_/i.test(a) || /^AREA_/i.test(b) || /^hkir_/i.test(a) || /^hkir_/i.test(b)) {
      return false;
    }
    return base(a) === base(b);
  },
  aliasQueries(q) {
    const f = BkkLib.fold(q);
    const extra = [];
    if (f.includes("arpad hid")) extra.push("Goncz Arpad varoskozpont");
    if (f.includes("moszkva")) extra.push("Szell Kalman ter");
    if (f === "keleti" || f.indexOf("keleti ") === 0) extra.push("Budapest-Keleti");
    if (f === "kelenfold" || f.indexOf("kelenfold ") === 0) extra.push("Budapest-Kelenfold");
    const key = BkkLib.stationKey(q);
    if (key && key !== q && extra.indexOf(key) < 0) extra.push(key);
    return extra;
  },
  labelAliased(q, hit) {
    const f = BkkLib.fold(q);
    const n = BkkLib.fold(hit.name);
    if (f.includes("arpad hid") && n === "goncz arpad varoskozpont") {
      return "Göncz Árpád városközpont (M3, Árpád híd)";
    }
    return hit.name;
  }
});

// src/config.js
var CARD_VERSION = "1.4.7-rev.3";
var BKK_PLANNER_TAG = "hungarian-transit-stop-card-plan";
var BKK_PLANNER_TAG_ALIAS = "bkk-stop-card-plan";
var BKK_API = "https://go.bkk.hu/api/query/v1/ws/otp/api/where";
var TRANSITOUS = "https://api.transitous.org";
var DEFAULT_MINUTES_AFTER = 180;
var MIN_MINUTES_AFTER = 15;
var MAX_MINUTES_AFTER = 480;
var MINUTES_AFTER_PRESETS = [30, 60, 90, 120, 180, 240, 360, 480];
var DEPART_MINUTES_AFTER = String(DEFAULT_MINUTES_AFTER);
var DEPARTURE_GRACE_SEC = 60;
function departureStillDue(dep, nowSec) {
  const ts = Number(dep);
  if (!Number.isFinite(ts) || ts <= 0) return true;
  return ts >= (nowSec || Math.floor(Date.now() / 1e3)) - DEPARTURE_GRACE_SEC;
}
function clampMinutesAfter(value) {
  if (value == null || value === "") return DEFAULT_MINUTES_AFTER;
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_MINUTES_AFTER;
  return Math.min(MAX_MINUTES_AFTER, Math.max(MIN_MINUTES_AFTER, Math.round(n)));
}
var PRESENTATION_KEYS = [
  "name",
  "language",
  "type",
  "grid_options",
  "view_layout",
  "visibility",
  "layout_options",
  "card_mod"
];
var STRING_KEYS = ["name", "stopId", "stopName", "destKey", "destName", "destStopId", "city", "apiKey", "language", "stopKind", "destKind"];
function normalizeRouteIds(value) {
  if (Array.isArray(value)) return value.filter((v) => v != null && v !== "").map((v) => String(v).trim()).filter(Boolean);
  if (typeof value === "string") return value.split(",").map((v) => v.trim()).filter(Boolean);
  return [];
}
function normalizeCardConfig(config) {
  const cfg = Object.assign({}, config || {});
  cfg.routeIds = normalizeRouteIds(cfg.routeIds);
  STRING_KEYS.forEach((k) => {
    if (!(k in cfg) || typeof cfg[k] === "string") return;
    cfg[k] = cfg[k] == null ? "" : String(cfg[k]);
  });
  if (cfg.minutesAfter != null && cfg.minutesAfter !== "") cfg.minutesAfter = clampMinutesAfter(cfg.minutesAfter);
  return cfg;
}
function configDataKey(config) {
  const cfg = config || {};
  return JSON.stringify(Object.keys(cfg).filter((k) => PRESENTATION_KEYS.indexOf(k) < 0).sort().map((k) => [k, cfg[k]]));
}
var DEFAULT_REFRESH_S = 45;
var MIN_REFRESH_S = 20;
function refreshSeconds(value) {
  if (value == null || value === "") return DEFAULT_REFRESH_S;
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_REFRESH_S;
  return Math.min(3600, Math.max(MIN_REFRESH_S, Math.round(n)));
}
function maxDepartureRows(horizon) {
  const h = clampMinutesAfter(horizon);
  const cap = h >= 480 ? 80 : 40;
  return Math.min(cap, Math.max(12, Math.round(h / 2.5)));
}
var PLATFORM_IN_HEAD = /(?:\u00b7\s*)?(?:v\u00e1g|pl)\.(\S+)/i;
var PLATFORM_STRIP = /\s*(?:\u00b7\s*)?(?:v\u00e1g|pl)\.\S+/gi;
var BKK_FAVORITES = [
  { id: "BKK_CSF01131", name: "Keleti pályaudvar" },
  { id: "BKK_CS007896", name: "Kelenföld vasútállomás" }
];
var MAV_FAVORITES = [
  { id: "BKK_005510017", name: "Budapest-Keleti" },
  { id: "BKK_005501024", name: "Budapest-Kelenföld" },
  { id: "BKK_005503269", name: "Székesfehérvár" }
];
var VOLAN_FAVORITES = [
  { id: "hkir_773521", name: "Budapest, Népliget autóbusz-pályaudvar" },
  { id: "volan_773538_99", name: "Budapest, Kelenföld vasútállomás" }
];
var FAVORITES = BKK_FAVORITES.concat(VOLAN_FAVORITES, MAV_FAVORITES);
var DEFAULT_FAVORITES = {
  bkk: BKK_FAVORITES,
  mav: MAV_FAVORITES,
  volan: VOLAN_FAVORITES,
  helyi: [],
  all: FAVORITES
};
var VEHICLE_ICONS = {
  BUS: "mdi:bus",
  TRAM: "mdi:tram",
  TROLLEYBUS: "mdi:bus-electric",
  SUBWAY: "mdi:subway-variant",
  METRO: "mdi:subway-variant",
  RAIL: "mdi:train",
  TRAIN: "mdi:train",
  FERRY: "mdi:ferry"
};
function vehicleIcon(type) {
  const key = String(type || "").toUpperCase();
  if (VEHICLE_ICONS[key]) return VEHICLE_ICONS[key];
  if (key.indexOf("RAIL") >= 0 || key.indexOf("TRAIN") >= 0) return "mdi:train";
  if (key.indexOf("TRAM") >= 0) return "mdi:tram";
  if (key.indexOf("TROLLEY") >= 0) return "mdi:bus-electric";
  if (key.indexOf("SUBWAY") >= 0 || key.indexOf("METRO") >= 0) return "mdi:subway-variant";
  return "mdi:bus";
}
var BKK_RAIL_BADGE = {
  G43: ["AACD46", "FFFFFF"],
  Z30: ["FFCD28", "3C3C3C"],
  S10: ["00AFF0", "FFFFFF"],
  S12: ["00AFF0", "FFFFFF"],
  S30: ["00AFF0", "FFFFFF"],
  S36: ["00AFF0", "FFFFFF"],
  S40: ["00AFF0", "FFFFFF"],
  IC: ["2E5EA8", "FFFFFF"],
  EC: ["2E5EA8", "FFFFFF"],
  SZ: ["5C6B7A", "FFFFFF"]
};
var ASSET_BASE = new URL(".", import.meta.url).href;
var VOLAN_INDEX_OVERRIDE = "";
var CITY_INDEX_OVERRIDE = "";
var MAV_INDEX_OVERRIDE = "";
var TZ = "Europe/Budapest";
var TZ_PARTS = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ,
  hour12: false,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
});
var pad2 = (n) => String(n).padStart(2, "0");
function tzFields(date) {
  const out = {};
  TZ_PARTS.formatToParts(date).forEach((p) => {
    if (p.type !== "literal") out[p.type] = p.value;
  });
  return {
    y: Number(out.year),
    mo: Number(out.month),
    d: Number(out.day),
    h: Number(out.hour) % 24,
    mi: Number(out.minute)
  };
}
function tzParts(date) {
  const f = tzFields(date);
  return { ymd: `${f.y}${pad2(f.mo)}${pad2(f.d)}`, mins: f.h * 60 + f.mi };
}
function tzEpochSec(ymd, mins) {
  const y = Number(ymd.slice(0, 4));
  const mo = Number(ymd.slice(4, 6));
  const d = Number(ymd.slice(6, 8));
  const target = Date.UTC(y, mo - 1, d) + (mins || 0) * 6e4;
  let utc = target;
  for (let i = 0; i < 2; i++) {
    const f = tzFields(new Date(utc));
    utc += target - Date.UTC(f.y, f.mo - 1, f.d, f.h, f.mi);
  }
  return Math.round(utc / 1e3);
}
function tzShiftYmd(ymd, days) {
  const y = Number(ymd.slice(0, 4));
  const mo = Number(ymd.slice(4, 6));
  const d = Number(ymd.slice(6, 8));
  const t2 = new Date(Date.UTC(y, mo - 1, d + (days || 0)));
  return `${t2.getUTCFullYear()}${pad2(t2.getUTCMonth() + 1)}${pad2(t2.getUTCDate())}`;
}
function tzDow(ymd) {
  const y = Number(ymd.slice(0, 4));
  const mo = Number(ymd.slice(4, 6));
  const d = Number(ymd.slice(6, 8));
  const js = new Date(Date.UTC(y, mo - 1, d)).getUTCDay();
  return js === 0 ? 6 : js - 1;
}
function dayBuster(base) {
  return `${base}${base.includes("?") ? "&" : "?"}v=${tzParts(/* @__PURE__ */ new Date()).ymd}`;
}
function volanIndexUrl(own) {
  const base = own || VOLAN_INDEX_OVERRIDE || new URL("volan-index.json.gz", ASSET_BASE).href;
  return dayBuster(base);
}
function cityIndexUrl(own) {
  const base = own || CITY_INDEX_OVERRIDE || new URL("city-index.json.gz", ASSET_BASE).href;
  return dayBuster(base);
}
function mavIndexUrl(own) {
  const base = own || MAV_INDEX_OVERRIDE || new URL("mav-index.json.gz", ASSET_BASE).href;
  return dayBuster(base);
}

// src/i18n.js
var I18N = {
  hu: {
    langAuto: "Automatikus (Home Assistant nyelve)",
    langHu: "Magyar",
    langEn: "English",
    langLabel: "Nyelv",
    apiKeyLabel: "BKK API kulcs",
    apiKeyPlaceholder: "UUID a BKK Open Datából",
    apiKeyHint: "Ingyenes kulcs, egyszerű regisztráció után:",
    apiKeyOk: "A BKK API kulcs rendben van.",
    apiKeyChange: "Módosítás",
    apiKeyShow: "Mutat",
    apiKeyHide: "Elrejt",
    apiKeyIntegration: "A BKK API kulcsot a Magyar közlekedés integráció adja.",
    apiKeyBorrowed: "A BKK API kulcsot egy másik kártya adja ezen a műszerfalon, ebbe a kártyába nem kerül be. A kártyán megadott kulcs elavult: tedd át a Magyar közlekedés integrációba.",
    apiKeyDeprecated: "A kártyán megadott kulcs elavult: minden bejelentkezett Home Assistant-felhasználó kiolvashatja a műszerfal beállításaiból, és a kérés címében megy a BKK-nak. Add meg inkább a Magyar közlekedés integrációban, és töröld innen.",
    nameLabel: "Név (opcionális)",
    namePlaceholder: "Magyar közlekedés",
    cardTitle: "Magyar közlekedés",
    helyiLabel: "Helyi",
    destLoading: "Elérhető célok betöltése...",
    destEmptySuggest: (list) => "Nincs közvetlen járat erre a célra. Elérhető célok: " + list + ".",
    tipPlatform: (p) => `Vágány ${p}`,
    tipStand: (p) => `Megállóhely ${p}`,
    minutes: (n) => `${n} perc`,
    now: "most",
    cardNoDepartures: "Nincs közelgő indulás.",
    cardPickStops: "Válassz forrás és cél megállót a kártya beállításaiban.",
    cardLoading: "Betöltés…",
    tipWheelchair: "Akadálymentes, alacsonypadlós jármű",
    tipBike: "Kerékpár szállítható",
    tipBooking: "Helyjegy váltása kötelező",
    tipBookingFreeSection: "a kijelölt belföldi szakaszon 2. osztályon helyjegy nélkül is",
    tipNationalPass: "Országbérlettel igénybe vehető",
    tipDelay: (n) => `${n} perc késés`,
    tipEarly: (n) => `${n} perccel korábban indul`,
    tipScheduled: (hm) => `Menetrend szerint ${hm}`,
    tipTravel: (n) => `Menetidő a célig: ${n} perc`,
    tipTrain: (nr) => `Vonatszám: ${nr}`,
    mapOpenTip: "Térkép: jármű az útvonalon",
    mapClose: "Bezárás",
    mapOk: "Rendben",
    mapLoadingRoute: "Útvonal betöltése…",
    mapNoData: "Ehhez a járathoz jelenleg nincs élő pozíció vagy útvonal.",
    mapLeafletFail: "A térkép betöltése nem sikerült.",
    mapNoGeometry: "A BKK nem adott vissza útvonal-geometriát ehhez a járathoz.",
    mapNothingToShow: "Nincs megjeleníthető útvonal ehhez a járathoz.",
    mapFullRoute: "Teljes útvonal",
    mapBoardStop: "Felszállás",
    mapAlightStop: "Leszállás",
    mapApproxLine: "Közelítő vonal: ehhez a járathoz nincs útvonaladat",
    mapTransferStop: "Átszállás",
    mapStartPoint: "Indulás",
    mapEndPoint: "Érkezés",
    mapLivePos: "Élő pozíció",
    mapEstPos: "Becsült pozíció (menetrend)",
    mapNoGps: "Nincs élő GPS",
    mapUntilStop: (n) => `Megállóig: ${n}%`,
    mapExpected: (hm) => `Várható: ${hm}`,
    mapExpectedVs: (pair) => `Várható: ${pair[0]} (menetrend ${pair[1]})`,
    mapFootFallback: "Jármű a térképen",
    mapLiveDot: "Élő pozíció",
    mapEstDot: "Becsült pozíció",
    mapTypeBus: "Busz",
    mapTypeTram: "Villamos",
    mapTypeTrolley: "Trolibusz",
    mapTypeSubway: "Metró",
    mapTypeRail: "Vonat",
    mapTypeCoach: "Távolsági busz",
    mapTypeFerry: "Hajó",
    mapTypeVehicle: "Jármű",
    errNoApiKey: "Nincs BKK API kulcs: add meg a Magyar közlekedés integrációban vagy a kártyában.",
    errApiKeyBad: "A BKK API kulcs érvénytelen (401/403).",
    errApiKeyProbe: "A BKK API kulcsot nem sikerült ellenőrizni.",
    errVolanHttp: (status) => `Volán menetrend HTTP ${status}`,
    errVolanGunzip: "A böngésző nem tudja kicsomagolni a Volán menetrendet.",
    errCityHttp: (status) => `Helyi menetrend HTTP ${status}`,
    errCityGunzip: "A böngésző nem tudja kicsomagolni a helyi menetrendet.",
    errIndexFetch: "A menetrend-index nem töltődött le (időtúllépés vagy hálózati hiba).",
    errIndexParse: "A letöltött menetrend-index olvashatatlan.",
    errIndexSchema: (v) => `A menetrend-index újabb (v${v}), mint amit ez a kártya ismer. Frissítsd a kártyát.`,
    cityLabel: "Város",
    cityPlaceholder: "Válassz várost",
    minutesAfterLabel: "Menetrend előre (perc)",
    minutesAfterHint: ([lo, hi, def]) => `Hány percet nézzen előre az indulásokból. ${lo}–${hi} perc, alapértelmezés ${def}.`,
    plannerTitle: "Tervező",
    plannerStep1: "Forrás",
    plannerStep2: "Cél",
    plannerStep3: "Meddig",
    plannerSwap: "Csere",
    plannerDepartures: "Indulások",
    plannerSearchPlaceholder: "Utca, házszám, város, megálló...",
    plannerDestPlaceholder: "Utca, házszám, város, megálló...",
    plannerTypeDest: "Írj egy utcát, várost vagy megállót.",
    plannerAddress: "cím",
    plannerPlace: "hely",
    plannerReset: "Új",
    plannerPickAll: "Válassz forrás és cél megállót.",
    plannerJourneyTitle: "Átszállással",
    plannerJourneyDirect: "Átszállás nélkül",
    plannerJourneyWalk: "Gyalog",
    plannerJourneySummary: (parts) => `${parts[0]} perc · ${parts[1]} átszállás · ${parts[2]} perc gyaloglás · ${parts[3]} perc várakozás`,
    plannerWalk: (parts) => `Gyaloglás ${parts[0]} perc, ${parts[1]} m`,
    plannerWait: (min) => `Várakozás ${min} perc`,
    plannerStationWalk: (parts) => `Gyaloglás a vágányhoz ${parts[0]} perc, ${parts[1]} m`,
    plannerRide: (min) => `${min} perc`,
    plannerShowMap: "Térkép",
    plannerWhen: "Mikor",
    plannerNow: "Most",
    plannerDepartAt: "Indulás",
    plannerArriveBy: "Érkezés",
    plannerHorizon: (n) => n < 60 ? `${n} perc` : `${String(n / 60).replace(".", ",")} óra`,
    plannerRoutes: "Útvonalak",
    plannerRoutesFrom: (when) => `indulás ${when}`,
    plannerRoutesBy: (when) => `érkezés ${when}-ig`,
    plannerSearching: "Útvonalak keresése…",
    plannerNoJourneys: "Erre az időpontra nem találtam útvonalat.",
    plannerDuration: (min) => {
      const n = Math.max(0, Math.round(Number(min) || 0));
      if (n < 60) return `${n} perc`;
      return n % 60 ? `${Math.floor(n / 60)} ó ${n % 60} p` : `${n / 60} óra`;
    },
    plannerTransfers: (n) => Number(n) > 0 ? `${n} átszállás` : "átszállás nélkül",
    plannerWalkTotal: (min) => `${min} perc gyaloglás`,
    plannerToday: "ma",
    plannerTomorrow: "holnap",
    mode: {
      bkk: {
        stop: "Forrás megálló",
        dest: "Cél megálló",
        hint: "Add meg a forrás és a cél megállót. Az összes BKK járat megjelenik közöttük.",
        originPh: "Keresés, pl. Bosnyák tér, Árpád híd",
        destPh: "Keresés a forrásból elérhető megállók között",
        empty: "Nincs BKK megálló erre a keresésre.",
        destEmpty: "Erről a megállóról nincs járat erre a célra.",
        destNeedOrigin: "Előbb válassz forrás megállót.",
        destNone: "Nincs elérhető BKK cél ebből a forrásból."
      },
      all: {
        stop: "Forrás megálló",
        dest: "Cél megálló",
        hint: "Válassz forrás és cél megállót. Minden BKK, Volán és MÁV járat megjelenik közöttük.",
        originPh: "Utca, megálló, autóbusz-állomás...",
        destPh: "Cél megálló keresése...",
        empty: "Nincs megálló erre a keresésre.",
        destEmpty: "Nincs járat ebből a megállóból oda.",
        destNeedOrigin: "Előbb válassz forrás megállót.",
        destNone: "Ebből a forrásból nincs közvetlen cél."
      },
      mav: {
        stop: "Forrás állomás",
        dest: "Cél állomás",
        hint: "Add meg a forrás és a cél állomást. Az összes vonat megjelenik közöttük.",
        originPh: "Keresés, pl. Kelenföld, Székesfehérvár",
        destPh: "Keresés a forrásból elérhető állomások között",
        empty: "Nincs MÁV állomás erre a keresésre.",
        destEmpty: "Erről az állomásról nincs vonat erre a célra.",
        destNeedOrigin: "Előbb válassz forrás állomást.",
        destNone: "Nincs elérhető MÁV célállomás ebből a forrásból."
      },
      volan: {
        stop: "Forrás megálló",
        dest: "Cél megálló",
        hint: "Add meg a forrás és a cél Volán megállót. Az összes busz megjelenik közöttük.",
        originPh: "Keresés, pl. Népliget, Székesfehérvár autóbusz-állomás",
        destPh: "Keresés a forrásból elérhető Volán megállók között",
        empty: "Nincs Volán megálló erre a keresésre.",
        destEmpty: "Erről a megállóról nincs közvetlen Volán erre a célra.",
        destNeedOrigin: "Előbb válassz forrás megállót.",
        destNone: "Nincs elérhető Volán cél ebből a forrásból."
      },
      helyi: {
        stop: "Forrás megálló",
        dest: "Cél megálló",
        hint: "Válassz várost, majd a forrás és a cél megállót. A helyi járatok jelennek meg közöttük.",
        originPh: "Keresés a város megállói között",
        destPh: "Keresés a forrásból elérhető megállók között",
        empty: "Nincs megálló erre a keresésre ebben a városban.",
        destEmpty: "Erről a megállóról nincs közvetlen járat erre a célra.",
        destNeedOrigin: "Előbb válassz forrás megállót.",
        destNone: "Nincs elérhető cél ebből a forrásból."
      }
    }
  },
  en: {
    langAuto: "Automatic (Home Assistant language)",
    langHu: "Magyar",
    langEn: "English",
    langLabel: "Language",
    apiKeyLabel: "BKK API key",
    apiKeyPlaceholder: "UUID from BKK Open Data",
    apiKeyHint: "Free key, after a quick sign-up:",
    apiKeyOk: "The BKK API key is working.",
    apiKeyChange: "Change",
    apiKeyShow: "Show",
    apiKeyHide: "Hide",
    apiKeyIntegration: "The BKK API key comes from the Hungarian transport integration.",
    apiKeyBorrowed: "The BKK API key comes from another card on this dashboard and is not saved into this card. A key on a card is deprecated: move it into the Hungarian transport integration.",
    apiKeyDeprecated: "A key on the card is deprecated: every logged-in Home Assistant user can read it from the dashboard config, and it is sent to BKK in the request URL. Enter it in the Hungarian transport integration instead and remove it here.",
    nameLabel: "Name (optional)",
    namePlaceholder: "Hungarian transport",
    cardTitle: "Hungarian transport",
    helyiLabel: "Local",
    destLoading: "Loading reachable destinations...",
    destEmptySuggest: (list) => "No direct service to that destination. Reachable destinations: " + list + ".",
    tipPlatform: (p) => `Platform ${p}`,
    tipStand: (p) => `Stop ${p}`,
    minutes: (n) => `${n} min`,
    now: "now",
    cardNoDepartures: "No upcoming departures.",
    cardPickStops: "Pick an origin and a destination stop in the card settings.",
    cardLoading: "Loading…",
    tipWheelchair: "Wheelchair accessible, low-floor vehicle",
    tipBike: "Bikes allowed",
    tipBooking: "Seat reservation required",
    tipBookingFreeSection: "not needed in 2nd class on the marked domestic section",
    tipNationalPass: "Valid with the national pass (Országbérlet)",
    tipDelay: (n) => `${n} min late`,
    tipEarly: (n) => `${n} min early`,
    tipScheduled: (hm) => `Scheduled for ${hm}`,
    tipTravel: (n) => `Travel time to destination: ${n} min`,
    tipTrain: (nr) => `Train number: ${nr}`,
    mapOpenTip: "Map: vehicle on its route",
    mapClose: "Close",
    mapOk: "OK",
    mapLoadingRoute: "Loading route…",
    mapNoData: "No live position or route is available for this trip.",
    mapLeafletFail: "Could not load the map.",
    mapNoGeometry: "BKK did not return a route shape for this trip.",
    mapNothingToShow: "Nothing to show on the map for this trip.",
    mapFullRoute: "Full route",
    mapBoardStop: "Boarding",
    mapAlightStop: "Alighting",
    mapApproxLine: "Approximate line: no route data for this trip",
    mapTransferStop: "Transfer",
    mapStartPoint: "Start",
    mapEndPoint: "Arrival",
    mapLivePos: "Live position",
    mapEstPos: "Estimated position (timetable)",
    mapNoGps: "No live GPS",
    mapUntilStop: (n) => `To stop: ${n}%`,
    mapExpected: (hm) => `Due: ${hm}`,
    mapExpectedVs: (pair) => `Due: ${pair[0]} (scheduled ${pair[1]})`,
    mapFootFallback: "Vehicle on the map",
    mapLiveDot: "Live position",
    mapEstDot: "Estimated position",
    mapTypeBus: "Bus",
    mapTypeTram: "Tram",
    mapTypeTrolley: "Trolleybus",
    mapTypeSubway: "Metro",
    mapTypeRail: "Train",
    mapTypeCoach: "Coach",
    mapTypeFerry: "Ferry",
    mapTypeVehicle: "Vehicle",
    errNoApiKey: "No BKK API key: set it in the Hungarian transport integration or in the card.",
    errApiKeyBad: "The BKK API key is invalid (401/403).",
    errApiKeyProbe: "Could not verify the BKK API key.",
    errVolanHttp: (status) => `Volán timetable HTTP ${status}`,
    errVolanGunzip: "This browser cannot decompress the Volán timetable.",
    errCityHttp: (status) => `Local timetable HTTP ${status}`,
    errCityGunzip: "This browser cannot decompress the local timetable.",
    errIndexFetch: "Could not download the timetable index (timeout or network error).",
    errIndexParse: "The downloaded timetable index is unreadable.",
    errIndexSchema: (v) => `The timetable index is newer (v${v}) than this card understands. Please update the card.`,
    cityLabel: "City",
    cityPlaceholder: "Pick a city",
    minutesAfterLabel: "Look-ahead (minutes)",
    minutesAfterHint: ([lo, hi, def]) => `How far ahead to list departures. ${lo}–${hi} minutes, default ${def}.`,
    plannerTitle: "Planner",
    plannerStep1: "Origin",
    plannerStep2: "Destination",
    plannerStep3: "Look-ahead",
    plannerSwap: "Swap",
    plannerDepartures: "Departures",
    plannerSearchPlaceholder: "Street, house number, city, stop...",
    plannerDestPlaceholder: "Street, house number, city, stop...",
    plannerTypeDest: "Type a street, city or stop.",
    plannerAddress: "address",
    plannerPlace: "place",
    plannerReset: "Reset",
    plannerPickAll: "Pick an origin and a destination stop.",
    plannerJourneyTitle: "With a transfer",
    plannerJourneyDirect: "Direct",
    plannerJourneyWalk: "On foot",
    plannerJourneySummary: (parts) => `${parts[0]} min · ${Number(parts[1]) === 1 ? "1 transfer" : `${parts[1]} transfers`} · ${parts[2]} min walking · ${parts[3]} min waiting`,
    plannerWalk: (parts) => `Walk ${parts[0]} min, ${parts[1]} m`,
    plannerWait: (min) => `Wait ${min} min`,
    plannerStationWalk: (parts) => `Walk to the platform ${parts[0]} min, ${parts[1]} m`,
    plannerRide: (min) => `${min} min`,
    plannerShowMap: "Map",
    plannerWhen: "When",
    plannerNow: "Now",
    plannerDepartAt: "Depart",
    plannerArriveBy: "Arrive",
    plannerHorizon: (n) => n < 60 ? `${n} min` : `${n / 60} h`,
    plannerRoutes: "Routes",
    plannerRoutesFrom: (when) => `leaving ${when}`,
    plannerRoutesBy: (when) => `arriving by ${when}`,
    plannerSearching: "Finding routes…",
    plannerNoJourneys: "No route found for this time.",
    plannerDuration: (min) => {
      const n = Math.max(0, Math.round(Number(min) || 0));
      if (n < 60) return `${n} min`;
      return n % 60 ? `${Math.floor(n / 60)} h ${n % 60} min` : `${n / 60} h`;
    },
    plannerTransfers: (n) => {
      const k = Number(n) || 0;
      if (k <= 0) return "direct";
      return k === 1 ? "1 transfer" : `${k} transfers`;
    },
    plannerWalkTotal: (min) => `${min} min walking`,
    plannerToday: "today",
    plannerTomorrow: "tomorrow",
    mode: {
      bkk: {
        stop: "Origin stop",
        dest: "Destination stop",
        hint: "Pick an origin and a destination stop. Every BKK service between them is listed.",
        originPh: "Search, e.g. Bosnyák tér, Árpád híd",
        destPh: "Search among the stops reachable from the origin",
        empty: "No BKK stop matches this search.",
        destEmpty: "No service from this stop to that destination.",
        destNeedOrigin: "Pick an origin stop first.",
        destNone: "No BKK destination is reachable from this origin."
      },
      all: {
        stop: "Origin stop",
        dest: "Destination stop",
        hint: "Pick an origin and a destination stop. Every BKK, Volán and MÁV service between them is listed.",
        originPh: "Street, stop, coach station...",
        destPh: "Search destination stop...",
        empty: "No stop matches this search.",
        destEmpty: "No service from this stop to that destination.",
        destNeedOrigin: "Pick an origin stop first.",
        destNone: "No destination is reachable from this origin."
      },
      mav: {
        stop: "Origin station",
        dest: "Destination station",
        hint: "Pick an origin and a destination station. Every train between them is listed.",
        originPh: "Search, e.g. Kelenföld, Székesfehérvár",
        destPh: "Search among the stations reachable from the origin",
        empty: "No MÁV station matches this search.",
        destEmpty: "No train from this station to that destination.",
        destNeedOrigin: "Pick an origin station first.",
        destNone: "No MÁV destination is reachable from this origin."
      },
      volan: {
        stop: "Origin stop",
        dest: "Destination stop",
        hint: "Pick an origin and a destination Volán stop. Every coach between them is listed.",
        originPh: "Search, e.g. Népliget, Székesfehérvár autóbusz-állomás",
        destPh: "Search among the Volán stops reachable from the origin",
        empty: "No Volán stop matches this search.",
        destEmpty: "No direct coach from this stop to that destination.",
        destNeedOrigin: "Pick an origin stop first.",
        destNone: "No Volán destination is reachable from this origin."
      },
      helyi: {
        stop: "Origin stop",
        dest: "Destination stop",
        hint: "Pick a city, then an origin and a destination. Local services between them are listed.",
        originPh: "Search stops in this city",
        destPh: "Search among the stops reachable from the origin",
        empty: "No stop in this city matches the search.",
        destEmpty: "No direct service from this stop to that destination.",
        destNeedOrigin: "Pick an origin stop first.",
        destNone: "No destination is reachable from this origin."
      }
    }
  }
};
function codedError(key, arg) {
  const err = new Error(String(I18N.hu[key] instanceof Function ? I18N.hu[key](arg) : I18N.hu[key]));
  err.i18nKey = key;
  err.i18nArg = arg;
  return err;
}
function resolveLang(pref, hass) {
  if (pref === "hu" || pref === "en") return pref;
  const raw = hass && hass.language || (typeof navigator !== "undefined" ? navigator.language : "") || "hu";
  return String(raw).toLowerCase().startsWith("hu") ? "hu" : "en";
}
function t(lang, key, arg) {
  const dict = I18N[lang] || I18N.hu;
  const value = dict[key] !== void 0 ? dict[key] : I18N.hu[key];
  return typeof value === "function" ? value(arg) : value;
}
function modeCopy(lang, mode) {
  const dict = I18N[lang] || I18N.hu;
  return dict.mode[mode] || dict.mode.bkk;
}
function errText(lang, err) {
  if (err && err.i18nKey) return t(lang, err.i18nKey, err.i18nArg);
  return err && err.message ? err.message : String(err);
}

// src/lib/futar.js
var BOARD_TTL_MS = MIN_REFRESH_S * 1e3;
Object.assign(BkkLib, {
  _mem: {},
  _inflight: {},
  _gateActive: 0,
  _gateCards: 0,
  _gateWaiters: [],
  _gateFgWaiters: [],
  _boards: /* @__PURE__ */ new Map(),
  _boardInflight: /* @__PURE__ */ new Map(),
  /* One slot pair per card on the dashboard, between 4 and 24. */
  _gateLimit() {
    const cards = Math.max(1, BkkLib._gateCards || 1);
    return Math.min(24, Math.max(4, cards * 2));
  },
  /* Foreground calls are the departure boards. They jump ahead of trip-details
     so one card finishing does not hold up the other cards on the dashboard. */
  async _gate(foreground) {
    const busy = BkkLib._gateActive >= BkkLib._gateLimit();
    const fgWaiting = BkkLib._gateFgWaiters.length > 0;
    if (!busy && (foreground || !fgWaiting)) {
      BkkLib._gateActive += 1;
      return;
    }
    await new Promise((resolve) => {
      (foreground ? BkkLib._gateFgWaiters : BkkLib._gateWaiters).push(resolve);
    });
  },
  _ungate() {
    const next = BkkLib._gateFgWaiters.shift() || BkkLib._gateWaiters.shift();
    if (next) next();
    else BkkLib._gateActive = Math.max(0, BkkLib._gateActive - 1);
  },
  _cached(cache, key) {
    if (BkkLib._mem[key]) return BkkLib._mem[key];
    if (cache && cache[key]) {
      BkkLib._mem[key] = cache[key];
      return cache[key];
    }
    return void 0;
  },
  _store(cache, key, value) {
    BkkLib.cachePut(BkkLib._mem, key, value);
    if (cache && cache !== BkkLib._mem) BkkLib.cachePut(cache, key, value);
    return value;
  },
  /* Latest hass object from any card or editor, for integration FUTÁR calls. */
  _hass: null,
  integrationHasKey(hass) {
    const h = hass || BkkLib._hass;
    return !!(h && h.services && h.services.hungarian_transport && h.services.hungarian_transport.futar);
  },
  /* The only key the browser ever holds is one the user put on a card. With
     no such key, FUTÁR goes through the integration when it holds the key,
     and a key borrowed from a sibling card is only the last resort. */
  cardApiKey(cfg, hass, borrowedKey) {
    const own = String(cfg && cfg.apiKey || "").trim();
    if (own) return own;
    if (BkkLib.integrationHasKey(hass)) return "";
    return String(borrowedKey || "").trim();
  },
  /* FUTÁR is reachable with this card key, or through the integration. */
  hasFutar(apiKey, hass) {
    return !!String(apiKey || "").trim() || BkkLib.integrationHasKey(hass);
  },
  /* Same messages the service error carried before: "BKK <detail>" for an
     outage, a key-specific one when FUTÁR rejected the integration's key. */
  _integrationError(code, detail) {
    const why = String(detail || code);
    const err = new Error(code === "futar_auth" ? `BKK FUTÁR rejected the configured key (${why}). Update it in the integration options.` : `BKK ${why}`);
    err.code = String(code);
    return err;
  },
  async _fetchViaIntegration(path, params) {
    const hass = BkkLib._hass;
    if (!BkkLib.integrationHasKey(hass)) throw codedError("errNoApiKey");
    const serviceData = { path: String(path), params: params || {} };
    let timer = null;
    const timeout = new Promise((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error("BKK timeout")), 2e4);
    });
    let res;
    try {
      const call = BkkLib.callServiceResponse(hass, "hungarian_transport", "futar", serviceData);
      res = await Promise.race([call, timeout]);
    } catch (err) {
      const msg = err && (err.message || err.code);
      throw new Error(msg && /^BKK /.test(String(msg)) ? String(msg) : `BKK ${msg || err}`);
    } finally {
      if (timer) clearTimeout(timer);
    }
    const payload = BkkLib.servicePayload(res);
    if (payload && payload.error) throw BkkLib._integrationError(payload.error, payload.detail);
    const data = payload && payload.data;
    if (!data || typeof data !== "object") throw new Error("BKK empty response");
    if (data.status && data.status !== "OK") throw new Error(`BKK ${data.status}`);
    return data;
  },
  /* With a card key the browser calls FUTÁR directly; without one, the
     integration's hungarian_transport.futar service does, with its own key.
     The card key path is DEPRECATED (review J-3): the key goes into the URL
     below and is readable from the dashboard config by every Home Assistant
     user. It still works; see BkkLib.warnCardApiKey. */
  async fetch(apiKey, path, params, foreground) {
    if (!apiKey) {
      if (!BkkLib.integrationHasKey()) throw codedError("errNoApiKey");
      await BkkLib._gate(!!foreground);
      try {
        return await BkkLib._fetchViaIntegration(path, params);
      } finally {
        BkkLib._ungate();
      }
    }
    await BkkLib._gate(!!foreground);
    const q = new URLSearchParams(Object.assign({
      key: apiKey,
      version: "4",
      appVersion: "apiary-1.0"
    }, params));
    const ctrl = typeof AbortController === "function" ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 2e4) : null;
    try {
      let res;
      try {
        res = await fetch(`${BKK_API}/${path}?${q}`, { signal: ctrl ? ctrl.signal : void 0 });
      } catch (err) {
        throw new Error(err && err.name === "AbortError" ? "BKK timeout" : `BKK ${err}`);
      }
      if (!res.ok) throw new Error(`BKK HTTP ${res.status}`);
      const data = await res.json();
      if (data.status && data.status !== "OK") throw new Error(`BKK ${data.status}`);
      return data;
    } finally {
      if (timer) clearTimeout(timer);
      BkkLib._ungate();
    }
  },
  /* Same stop, horizon and FUTÁR access is one request, shared by every card
     that needs it. A card with its own (revoked) key must not be served the
     board another card fetched with a working one, nor the other way round. */
  departureBoard(apiKey, stopId, horizon) {
    const via = String(apiKey || "").trim();
    const key = (via ? "key:" + via : "integration") + "|" + String(stopId) + "|" + String(horizon);
    const hit = BkkLib._boards.get(key);
    if (hit && Date.now() - hit.ts < BOARD_TTL_MS) return Promise.resolve(hit.data);
    const inflight = BkkLib._boardInflight.get(key);
    if (inflight) return inflight;
    const run = BkkLib.fetch(apiKey, "arrivals-and-departures-for-stop.json", {
      stopId: String(stopId),
      minutesAfter: String(horizon),
      minutesBefore: "0",
      onlyDepartures: "true",
      includeReferences: "true",
      includeVehicleFromTrip: "true"
    }, true).then((data) => {
      const now = Date.now();
      BkkLib._boards.forEach((v, k) => {
        if (now - v.ts >= BOARD_TTL_MS) BkkLib._boards.delete(k);
      });
      BkkLib._boards.set(key, { ts: now, data });
      return data;
    }).finally(() => {
      BkkLib._boardInflight.delete(key);
    });
    BkkLib._boardInflight.set(key, run);
    return run;
  },
  boardSpanMin(data, nowSec) {
    const times = (((data || {}).data || {}).entry || {}).stopTimes || [];
    let last = 0;
    times.forEach((st) => {
      const dep = st.predictedDepartureTime || st.departureTime || 0;
      if (dep > last) last = dep;
    });
    if (!last) return { count: times.length, spanMin: 0 };
    return { count: times.length, spanMin: (last - nowSec) / 60 };
  },
  planPlaceVertex(name, id) {
    const label = String(name || "").replace(/::/g, " ").trim();
    const raw = String(id || "");
    const m = raw.match(/^BKK_(.+)$/i);
    if (m && label) return `${label}::BKK:${m[1]}`;
    return "";
  },
  async planPlace(apiKey, stop) {
    const name = stop && stop.name || "";
    const id = stop && stop.id || "";
    const vertex = BkkLib.planPlaceVertex(name, id);
    if (vertex) return vertex;
    const pt = BkkLib.mapGpsLatLon(stop && stop.lat, stop && stop.lon);
    if (name && pt) return `${name}::${pt[0]},${pt[1]}`;
    if (!BkkLib.hasFutar(apiKey) || !name) return "";
    try {
      const data = await BkkLib.fetch(apiKey, "search.json", { query: name }, true);
      const stops = ((data.data || {}).references || {}).stops || {};
      const want = BkkLib.fold(name);
      let best = null;
      Object.values(stops).forEach((s) => {
        if (!s || s.lat == null || s.lon == null) return;
        if (id && s.id === id) best = s;
        else if (!best && BkkLib.fold(s.name) === want) best = s;
      });
      if (!best) return "";
      return `${String(name).replace(/::/g, " ")}::${best.lat},${best.lon}`;
    } catch (_e) {
      return "";
    }
  }
});

// src/lib/transitous.js
Object.assign(BkkLib, {
  /* FUTÁR plan-trip times are epoch milliseconds. Values below 1e11 are seconds. */
  epochMs(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return 0;
    return n >= 1e11 ? n : n * 1e3;
  },
  isoMs(value) {
    if (value == null || value === "") return 0;
    if (typeof value === "number") return BkkLib.epochMs(value);
    const n = Date.parse(String(value));
    return Number.isFinite(n) && n > 0 ? n : 0;
  },
  async transitousFetch(path, params) {
    const q = new URLSearchParams();
    Object.keys(params || {}).forEach((key) => {
      const val = params[key];
      if (val == null || val === "") return;
      q.set(key, String(val));
    });
    const ctrl = typeof AbortController === "function" ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), 1e4) : null;
    const base = String(TRANSITOUS).replace(/\/$/, "");
    const rel = String(path || "").indexOf("/") === 0 ? String(path) : "/" + String(path || "");
    try {
      const res = await fetch(`${base}${rel}?${q}`, {
        cache: "no-store",
        signal: ctrl ? ctrl.signal : void 0,
        headers: {
          Accept: "application/json"
        }
      });
      if (!res.ok) throw new Error(`Transitous HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      if (err && err.name === "AbortError") throw new Error("Transitous timeout");
      throw err;
    } finally {
      if (timer) clearTimeout(timer);
    }
  },
  bkkTransitousStopIds(id) {
    const raw = String(id || "").trim();
    if (!/^BKK_/i.test(raw) || /^BKK_0055/i.test(raw)) return [];
    const body = raw.replace(/^BKK_/i, "");
    const ids = ["hu-bkk_" + body];
    if (/^CSF/i.test(body) && body.length > 3) ids.push("hu-bkk_F" + body.slice(3));
    return ids;
  },
  transitousFeedWant(id) {
    const s = String(id || "");
    if (/^BKK_0055/i.test(s)) return "hu-mav_";
    if (/^BKK_/i.test(s)) return "hu-bkk_";
    if (/^(volan_|AREA_CS|hkir_)/i.test(s)) return "hu-volanbusz_";
    if (/^miskolc:/i.test(s)) return "hu-mvk_";
    if (s.indexOf(":") > 0) return "hu-";
    return "";
  },
  /* A stop does not move: every poll of an empty board geocoded both ends
     again. Answers stay for the page; a failed lookup is asked again, and
     an empty answer after ten minutes (a geocoder that had no data yet). */
  _geocodeCache: /* @__PURE__ */ new Map(),
  _geocodeCacheMax: 200,
  _geocodeEmptyAt: /* @__PURE__ */ new Map(),
  _geocodeEmptyRetryMs: 10 * 60 * 1e3,
  transitousGeocode(text, opts) {
    const q = String(text || "").trim();
    if (!q) return Promise.resolve([]);
    const params = { text: q, language: "hu" };
    const typ = opts && opts.type;
    if (typ === "all") {
    } else {
      params.type = typ || "STOP";
    }
    const key = `${params.type || "all"}|${q}`;
    const cache = BkkLib._geocodeCache;
    const empties = BkkLib._geocodeEmptyAt;
    const hit = cache.get(key);
    const emptyAt = empties.get(key);
    if (hit && !(emptyAt && Date.now() - emptyAt >= BkkLib._geocodeEmptyRetryMs)) return hit;
    if (hit) cache.delete(key);
    empties.delete(key);
    const run = BkkLib.transitousFetch("/api/v1/geocode", params).then((data) => {
      const list = Array.isArray(data) ? data : [];
      if (!list.length && cache.get(key) === run) empties.set(key, Date.now());
      return list;
    });
    run.catch(() => {
      if (cache.get(key) === run) cache.delete(key);
    });
    if (cache.size >= BkkLib._geocodeCacheMax) {
      const oldest = cache.keys().next().value;
      cache.delete(oldest);
      empties.delete(oldest);
    }
    cache.set(key, run);
    return run;
  },
  transitousInHungary(hit) {
    if (!hit) return false;
    if (/^hu-/i.test(String(hit.id || ""))) return true;
    if ((hit.areas || []).some((a) => /magyarorsz/i.test(a && a.name || ""))) return true;
    const lat = Number(hit.lat);
    const lon = Number(hit.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false;
    return lat >= 45.7 && lat <= 48.65 && lon >= 16.05 && lon <= 22.95;
  },
  isPlaceStop(stop) {
    if (!stop) return false;
    if (/^(geo:|node\/|way\/)/i.test(String(stop.id || stop.stopId || ""))) return true;
    const kind = String(stop.kind || stop.stopKind || "");
    return kind === "address" || kind === "place";
  },
  transitousHitToStop(hit) {
    if (!hit || !hit.name) return null;
    const lat = Number(hit.lat);
    const lon = Number(hit.lon);
    const areas = hit.areas || [];
    const city = (areas.find((a) => Number(a.adminLevel) === 8) || areas.find((a) => a.unique) || {}).name || "";
    const kindRaw = String(hit.type || "").toUpperCase();
    const kind = kindRaw === "ADDRESS" ? "address" : kindRaw === "PLACE" ? "place" : "stop";
    let id = String(hit.id || "");
    if (!id && Number.isFinite(lat) && Number.isFinite(lon)) {
      id = "geo:" + lat.toFixed(6) + "," + lon.toFixed(6);
    }
    if (!id) return null;
    return {
      id,
      name: hit.name,
      label: city ? hit.name + " (" + city + ")" : hit.name,
      lat: Number.isFinite(lat) ? lat : void 0,
      lon: Number.isFinite(lon) ? lon : void 0,
      kind
    };
  },
  plannerHitLabel(s, lang) {
    const tag = s && s.kind === "address" ? t(lang, "plannerAddress") : s && s.kind === "place" ? t(lang, "plannerPlace") : "";
    const base = s && (s.label || s.name) || "";
    return tag ? base + " · " + tag : base;
  },
  async searchStopsTransitous(q, opts) {
    const hits = await BkkLib.transitousGeocode(q, opts);
    return (hits || []).filter((hit) => BkkLib.transitousInHungary(hit)).map((hit) => BkkLib.transitousHitToStop(hit)).filter((s) => s && s.id).slice(0, 20);
  },
  capPlannerHits(hits, n) {
    const limit = n || 24;
    const volan = [];
    const rest = [];
    (hits || []).forEach((s) => {
      if (!s || !s.id) return;
      if (/^(?:volan_|hkir_|AREA_CS)/i.test(String(s.id))) volan.push(s);
      else rest.push(s);
    });
    const out = [];
    const max = Math.max(volan.length, rest.length);
    for (let i = 0; i < max && out.length < limit; i++) {
      if (i < rest.length) out.push(rest[i]);
      if (out.length >= limit) break;
      if (i < volan.length) out.push(volan[i]);
    }
    return out;
  },
  async searchPlannerStops(apiKey, q) {
    const byId = /* @__PURE__ */ new Map();
    try {
      const motis = await BkkLib.searchStopsTransitous(q, { type: "all" });
      motis.forEach((s) => {
        if (!s || !s.id || /^hu-volanbusz_/i.test(s.id)) return;
        const mapped = String(s.id).replace(/^hu-bkk_/i, "BKK_");
        const row = mapped !== s.id ? Object.assign({}, s, { id: mapped }) : s;
        if (byId.has(row.id)) return;
        byId.set(row.id, row);
      });
      await this.addTransitousVolanStops(byId, q, motis);
    } catch (_e) {
    }
    try {
      (await this.searchStops(apiKey, q, "all", "", { skipTransitousVolan: true })).forEach((s) => {
        if (!s || !s.id || byId.has(s.id)) return;
        byId.set(s.id, s);
      });
    } catch (_e) {
    }
    const placed = await this.withGtfsCoords(Array.from(byId.values()));
    const collapsed = BkkLib.collapseSameNameStops(placed);
    return BkkLib.capPlannerHits(collapsed, 24);
  },
  pickTransitousHit(hits, stop, kind) {
    const list = Array.isArray(hits) ? hits : [];
    if (!list.length) return null;
    const want = BkkLib.transitousFeedWant(stop && stop.id);
    const wantName = stop && stop.name || "";
    kind = String(kind || "").toLowerCase();
    const area = BkkLib.fold(stop && stop.area || "");
    const inArea = (hit) => !!area && (hit.areas || []).some((a) => BkkLib.fold(a && a.name) === area);
    const areaHits = area ? list.filter((hit) => hit && inArea(hit)) : [];
    const busWanted = /aut[o\u00f3]busz|aut\.?\s*\u00e1ll/i.test(wantName);
    const near = BkkLib.mapGpsLatLon(stop && stop.lat, stop && stop.lon);
    let best = null;
    let bestScore = -1;
    list.forEach((hit) => {
      if (!hit) return;
      if (areaHits.length && !inArea(hit)) return;
      const hid = String(hit.id || "");
      const hname = hit.name || "";
      let score = 0;
      const railWant = want === "hu-mav_" || kind === "rail" || kind === "train";
      if (railWant && (/railway/i.test(hid) || hid.indexOf("hu-mav_") === 0)) score += 8;
      else if (want && hid.indexOf(want) === 0) score += 5;
      else if (railWant && hid.indexOf("hu-bkk_") === 0) score += 2;
      else if (/^hu-/.test(hid)) score += 1;
      if (/railway/i.test(hid) && railWant) score += 2;
      const wantTok = BkkLib.foldTokens(wantName);
      const hitTok = BkkLib.foldTokens(hname);
      const missing = wantTok.filter((tok) => !hitTok.some((y) => y === tok || y.indexOf(tok) >= 0 || tok.indexOf(y) >= 0));
      if (missing.length) score -= 4 * missing.length;
      if (BkkLib.foldTokenMatch(wantName, hname)) score += 3;
      if (BkkLib.fold(wantName) && BkkLib.fold(hname) === BkkLib.fold(wantName)) score += 2;
      if (!busWanted && /aut[o\u00f3]busz|aut\.?\s*\u00e1ll/i.test(hname)) score -= 4;
      const hpt = near && BkkLib.mapGpsLatLon(hit.lat, hit.lon);
      if (hpt) {
        const d = BkkLib.haversineMeters(near, hpt);
        if (d <= 400) score += 6;
        else if (d > 5e3) score -= 6;
      }
      if (score > bestScore) {
        bestScore = score;
        best = hit;
      }
    });
    if (bestScore >= 3) return best;
    return (areaHits.length ? areaHits[0] : list[0]) || null;
  },
  transitousCoord(hit) {
    if (!hit) return "";
    const lat = Number(hit.lat);
    const lon = Number(hit.lon);
    if (Number.isFinite(lat) && Number.isFinite(lon)) return lat + "," + lon;
    return String(hit.id || "");
  },
  async transitousPlace(stop, kind) {
    if (!stop) return "";
    const pt = BkkLib.mapGpsLatLon(stop.lat, stop.lon);
    if (pt) return pt[0] + "," + pt[1];
    const name = String(stop.name || "").trim();
    if (!name) return String(stop.id || "");
    try {
      const hits = await BkkLib.transitousGeocode(name);
      return BkkLib.transitousCoord(BkkLib.pickTransitousHit(hits, stop, kind));
    } catch (_e) {
      return "";
    }
  },
  async transitousStopRef(stop, kind) {
    const name = String(stop && (stop.name || stop.key) || "").trim();
    const fromStop = BkkLib.mapGpsLatLon(stop && stop.lat, stop && stop.lon) || BkkLib.knownStopCoords(stop);
    const mapped = BkkLib.bkkTransitousStopIds(stop && stop.id);
    for (let i = 0; i < mapped.length; i += 1) {
      try {
        const data = await BkkLib.transitousFetch("/api/v5/stoptimes", {
          stopId: mapped[i],
          n: "1"
        });
        const place = data && data.place;
        const pt = place && BkkLib.mapGpsLatLon(place.lat, place.lon);
        if (!pt) continue;
        const pname = String(place && place.name || "").trim();
        if (name && pname && !BkkLib.foldTokenMatch(name, pname) && BkkLib.fold(name) !== BkkLib.fold(pname)) {
          continue;
        }
        return {
          id: mapped[i],
          name: pname || name,
          lat: pt[0],
          lon: pt[1]
        };
      } catch (_e) {
      }
    }
    const queries = [];
    const area = String(stop && stop.area || "").trim();
    if (name && area && BkkLib.fold(name).indexOf(BkkLib.fold(area)) < 0) queries.push(`${name} ${area}`);
    if (name) queries.push(name);
    let stripped = name;
    [" vasútállomás", " pályaudvar", " pu."].forEach((sfx) => {
      const low = stripped.toLowerCase();
      if (low.endsWith(sfx)) stripped = stripped.slice(0, -sfx.length).trim();
    });
    if (stripped && stripped !== name) queries.push(stripped);
    const hits = [];
    const seen = {};
    for (let i = 0; i < queries.length; i += 1) {
      try {
        const found = await BkkLib.transitousGeocode(queries[i]);
        (found || []).forEach((hit2) => {
          const id = hit2 && hit2.id;
          if (!id || seen[id]) return;
          seen[id] = true;
          hits.push(hit2);
        });
      } catch (_e) {
      }
    }
    let hit = BkkLib.pickTransitousHit(hits, stop, kind);
    if ((kind === "rail" || kind === "train") && hit && !/railway|hu-mav_/i.test(String(hit.id || ""))) {
      const rail = hits.find((h) => /railway|hu-mav_/i.test(String(h && h.id || "")));
      if (rail) hit = rail;
    }
    const board = BkkLib.mapGpsLatLon(hit && hit.lat, hit && hit.lon) || fromStop;
    return {
      id: hit && hit.id || "",
      name: hit && hit.name || name,
      lat: board ? board[0] : null,
      lon: board ? board[1] : null
    };
  },
  motisLegLabel(leg) {
    const short = String(leg && leg.routeShortName || "").trim();
    if (short) return short;
    const display = String(leg && leg.displayName || "").trim();
    if (display) return display;
    return String(leg && leg.tripShortName || "").trim();
  },
  motisLegType(mode) {
    const m = String(mode || "").toUpperCase();
    if (m.indexOf("SUBURBAN") >= 0) return "SUBURBAN";
    if (/RAIL|TRAIN/.test(m)) return "RAIL";
    if (m.indexOf("TRAM") >= 0) return "TRAM";
    if (m.indexOf("SUBWAY") >= 0 || m.indexOf("METRO") >= 0) return "SUBWAY";
    if (m.indexOf("TROLLEY") >= 0) return "TROLLEYBUS";
    if (m === "COACH") return "COACH";
    return "BUS";
  },
  motisMatchesMode(leg, mode) {
    if (mode === "all" || mode === "helyi") return true;
    const m = String(leg && leg.mode || "").toUpperCase();
    if (m === "WALK") return false;
    const id = String(leg && (leg.tripId || leg.routeId) || "");
    return BkkLib.routeMatchesMode({
      type: BkkLib.motisLegType(m),
      id: /volan/i.test(id) ? "volan_" + id : id,
      agencyId: id
    }, mode || "all");
  },
  rowsFromMotis(payload, dest, mode, horizon, parseOpts) {
    const its = payload && payload.itineraries || [];
    parseOpts = parseOpts || {};
    const now = Number(parseOpts.now) > 0 ? Number(parseOpts.now) : Math.floor(Date.now() / 1e3);
    const h = clampMinutesAfter(horizon);
    const latest = now + h * 60;
    const destName = dest && (dest.name || dest.key) || "";
    const out = [];
    its.forEach((it) => {
      if (Number(it.transfers || 0) > 0) return;
      const ride = (it.legs || []).filter((leg2) => String(leg2.mode || "").toUpperCase() !== "WALK");
      if (ride.length !== 1) return;
      const leg = ride[0];
      if (!BkkLib.motisMatchesMode(leg, mode || "all")) return;
      const startMs = BkkLib.isoMs(leg.startTime || it.startTime);
      const dep = startMs ? Math.round(startMs / 1e3) : 0;
      if (!dep || dep > latest + 60) return;
      if (!parseOpts.keepPast && !departureStillDue(dep, now)) return;
      const endMs = BkkLib.isoMs(leg.endTime || it.endTime);
      const sec = Number(leg.duration);
      const fromSec = Number.isFinite(sec) && sec > 0 ? sec : startMs && endMs ? Math.round((endMs - startMs) / 1e3) : 0;
      const travel = fromSec <= 0 ? null : Math.max(1, Math.round(fromSec / 60));
      const rawType = BkkLib.motisLegType(leg.mode);
      const label = BkkLib.motisLegLabel(leg);
      const color = String(leg.routeColor || "").replace(/#/g, "") || (rawType === "RAIL" ? "4477AA" : "009EE3");
      const text = String(leg.routeTextColor || "").replace(/#/g, "") || "ffffff";
      const tripRaw = String(leg.tripId || "").trim();
      const tripId = "motis:" + (tripRaw || dep + ":" + label);
      const trainNumber = /RAIL|SUBURBAN|TRAIN/.test(rawType) ? BkkLib.emmaTrainNumber(leg.tripShortName || label) : "";
      const fromStop = leg.from || {};
      const plat = String(
        fromStop.scheduledTrack || fromStop.track || fromStop.platform || ""
      ).trim();
      const row = {
        tripId,
        motisTripId: tripRaw,
        label,
        color: "#" + color,
        text: "#" + text,
        vehicle: BkkLib.vehicleKind({ type: rawType, id: tripRaw }),
        rawType,
        dep,
        sched: dep,
        delay: 0,
        head: String(leg.headsign || destName || ""),
        platform: plat,
        wheelchair: false,
        bikesAllowed: false,
        booking: !!(leg.mayRequireBooking || fromStop.mayRequireBooking || leg.booking),
        trainNumber,
        travel,
        shape: BkkLib.decodePolyline(leg.legGeometry)
      };
      BkkLib.applyRailBadge(row);
      out.push(row);
    });
    out.sort((a, b) => (a.dep || 0) - (b.dep || 0));
    return out;
  },
  async transitousDepartures(origin, dest, opts) {
    opts = opts || {};
    if (!dest || !(dest.key || dest.name || dest.id)) return [];
    const [fromPlace, toPlace] = await Promise.all([
      BkkLib.transitousPlace(origin),
      BkkLib.transitousPlace(dest)
    ]);
    if (!fromPlace || !toPlace) return [];
    const horizon = clampMinutesAfter(opts.minutesAfter);
    const anchor = Number(opts.anchorDep || 0);
    try {
      const params = {
        fromPlace,
        toPlace,
        timetableView: "true",
        maxTransfers: "0",
        numItineraries: String(maxDepartureRows(horizon)),
        searchWindow: String(horizon * 60)
      };
      if (opts.time) params.time = String(opts.time);
      else if (anchor > 0) params.time = new Date(Math.max(0, anchor - 120) * 1e3).toISOString();
      const data = await BkkLib.transitousFetch("/api/v5/plan", params);
      const parseOpts = anchor > 0 ? { now: anchor, keepPast: true } : {};
      return BkkLib.rowsFromMotis(data, dest, opts.mode || "all", horizon, parseOpts);
    } catch (_e) {
      return [];
    }
  },
  isTrainRow(row) {
    return BkkLib.vehicleBucket(row) === "rail";
  },
  /* Hop maps draw the whole published line (street or track), not a
     stop-to-stop chord. Bus/tram/metro/trolley/coach/rail all keep it
     (8E: Újpalota → Kelenföld, 2B: Jászai → Pacsirtatelep, G43: the
     pfaedle rails). Leaflet canvas at GPS zoom clips that; SVG does not. */
  keepsFullLineShape(row) {
    if (BkkLib.isTrainRow(row)) return true;
    const kind = BkkLib.vehicleBucket(row);
    return kind === "tram" || kind === "subway" || kind === "trolley" || kind === "bus" || kind === "coach";
  },
  vehicleBucket(row) {
    if (!row) return "";
    if (typeof row === "string") {
      const s = row.toLowerCase();
      if (/rail|train|suburban/.test(s)) return "rail";
      if (/tram/.test(s)) return "tram";
      if (/subway|metro/.test(s)) return "subway";
      if (/trolley/.test(s)) return "trolley";
      if (/ferry|boat/.test(s)) return "ferry";
      if (/coach/.test(s)) return "coach";
      if (/bus/.test(s)) return "bus";
      return s;
    }
    const v = String(row.vehicle || "").toLowerCase();
    const typ = String(row.rawType || row.type || row.mode || "").toUpperCase();
    if (v === "rail" || v === "train" || /RAIL|SUBURBAN|TRAIN/.test(typ)) return "rail";
    if (/^elvira:/i.test(String(row.tripId || ""))) return "rail";
    if (v === "tram" || typ.indexOf("TRAM") >= 0) return "tram";
    if (v === "subway" || v === "metro" || /SUBWAY|METRO/.test(typ)) return "subway";
    if (v === "trolley" || v === "trolleybus" || typ.indexOf("TROLLEY") >= 0) return "trolley";
    if (v === "ferry" || typ.indexOf("FERRY") >= 0) return "ferry";
    if (v === "coach" || typ === "COACH" || /^gtfs:/i.test(String(row.tripId || ""))) return "coach";
    if (v === "bus" || typ === "BUS") return "bus";
    if (BkkLib.rowTrainNumber(row) && !/bus|tram|subway|trolley|coach/i.test(v)) return "rail";
    return v || "bus";
  },
  sameVehicleBucket(a, b) {
    const x = BkkLib.vehicleBucket(a);
    const y = BkkLib.vehicleBucket(b);
    if (!x || !y) return true;
    if (x === y) return true;
    return x === "bus" && y === "coach" || x === "coach" && y === "bus";
  },
  motisTripIdRaw(row) {
    const raw = String(row && (row.motisTripId || row.tripId) || "").replace(/^motis:/i, "").trim();
    if (!raw || /^\d+:/.test(raw)) return "";
    return raw;
  },
  namedTrainAliasMatch(a, b) {
    const aliases = { ADRIA: ["AGRAM"], AGRAM: ["ADRIA"] };
    const tokens = (s) => String(s || "").toUpperCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").split(/[^A-Z]+/).filter((t2) => t2.length >= 4 && !BkkLib.genericRailLabel(t2));
    const ta = tokens(a);
    const tb = tokens(b);
    for (let i = 0; i < ta.length; i += 1) {
      const al = aliases[ta[i]] || [];
      for (let j = 0; j < al.length; j += 1) {
        if (tb.indexOf(al[j]) >= 0) return true;
      }
    }
    return false;
  },
  motisLabelMatch(a, b) {
    const la = String(a || "").trim().toUpperCase();
    const lb = String(b || "").trim().toUpperCase();
    if (!la || !lb) return false;
    if (la === lb) return true;
    if (lb.indexOf(la + " ") === 0 || la.indexOf(lb + " ") === 0) return true;
    if (BkkLib.genericRailLabel(la) || BkkLib.genericRailLabel(lb)) return false;
    const fa = BkkLib.fold(la);
    const fb = BkkLib.fold(lb);
    if (!fa || !fb) return false;
    if (fa === fb) return true;
    const esc = fa.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp("(?:^|[^a-z0-9])" + esc + "(?:[^a-z0-9]|$)").test(fb);
  },
  destOnMotisRow(m, dest) {
    const want = BkkLib.fold(dest && (dest.name || dest.key) || "");
    if (!want) return false;
    const names = [];
    (m && m.nextNames || []).forEach((n) => names.push(n));
    if (m && m.tripTo) names.push(m.tripTo);
    return names.some((n) => {
      if (!n) return false;
      return BkkLib.fold(n) === want || BkkLib.foldTokenMatch(want, n);
    });
  },
  rowsFromMotisStopTimes(payload) {
    const sts = payload && payload.stopTimes || [];
    return sts.map((st) => {
      const place = st && st.place || {};
      const startMs = BkkLib.isoMs(place.departure || place.scheduledDeparture);
      const rawType = BkkLib.motisLegType(st.mode);
      const label = String(st.displayName || st.routeShortName || st.tripShortName || "").trim();
      const tripRaw = String(st && st.tripId || "").trim();
      const destName = st.tripTo && st.tripTo.name || "";
      return {
        tripId: tripRaw ? "motis:" + tripRaw : "",
        motisTripId: tripRaw,
        label,
        vehicle: BkkLib.vehicleKind({ type: rawType, id: tripRaw }),
        rawType,
        dep: startMs ? Math.round(startMs / 1e3) : 0,
        sched: startMs ? Math.round(startMs / 1e3) : 0,
        head: String(st.headsign || destName || ""),
        tripTo: destName,
        nextNames: (st.nextStops || []).map((p) => p && p.name).filter(Boolean),
        trainNumber: /RAIL|SUBURBAN|TRAIN/.test(rawType) ? BkkLib.emmaTrainNumber(st.tripShortName || label) : "",
        shape: []
      };
    }).filter((row) => row.motisTripId);
  },
  pickMotisVehicle(motis, row, dest) {
    const list = Array.isArray(motis) ? motis : [];
    const num = BkkLib.rowTrainNumber(row);
    const label = String(row && row.label || "").trim().toUpperCase();
    const dep = Number(row && (row.dep || row.sched || row.depTs) || 0);
    const wantsId = !!(num || label && !BkkLib.genericRailLabel(label));
    let best = null;
    let bestScore = -1e9;
    const usable = (m) => {
      if (!m) return false;
      if (!BkkLib.sameVehicleBucket(row, m)) return false;
      const shape = BkkLib.validMapShape(m.shape);
      return shape.length >= 2 || !!BkkLib.motisTripIdRaw(m);
    };
    list.forEach((m) => {
      if (!usable(m)) return;
      const mNum = BkkLib.rowTrainNumber(m);
      const mLabel = String(m && m.label || "").trim().toUpperCase();
      const sameNum = !!(num && mNum && num === mNum);
      const sameLabel = BkkLib.motisLabelMatch(label, mLabel);
      const sameAlias = !!(label && (BkkLib.namedTrainAliasMatch(label, mLabel) || BkkLib.namedTrainAliasMatch(label, String(m && m.head || ""))));
      const destOn = BkkLib.destOnMotisRow(m, dest);
      const dt = dep ? Math.abs(Number(m.dep || 0) - dep) : 0;
      if (dep && dt > 45 * 60 && !sameNum) return;
      const identity = sameNum || sameLabel || sameAlias;
      if (!identity && wantsId) return;
      const destNear = destOn && (!dep || dt <= 12 * 60);
      if (!identity && !destNear) return;
      const score = (sameNum ? 1e3 : 0) + (sameLabel ? 400 : 0) + (sameAlias ? 250 : 0) + (destNear ? 50 : 0) - Math.min(dt, 1800) / 60;
      if (score > bestScore) {
        best = m;
        bestScore = score;
      }
    });
    if (!best && dep) {
      const near = list.filter((m) => usable(m) && Math.abs(Number(m.dep || 0) - dep) <= 8 * 60);
      const toward = dest ? near.filter((m) => BkkLib.destOnMotisRow(m, dest)) : near;
      const pool = toward.length ? toward : near;
      if (pool.length === 1) {
        const dt0 = Math.abs(Number(pool[0].dep || 0) - dep);
        if (!wantsId || BkkLib.isTrainRow(row) && dt0 <= 90) {
          best = pool[0];
        }
      } else if (pool.length > 1 && !wantsId) {
        pool.sort((a, b) => Math.abs(Number(a.dep || 0) - dep) - Math.abs(Number(b.dep || 0) - dep));
        const gap = Math.abs(Number(pool[1].dep || 0) - dep) - Math.abs(Number(pool[0].dep || 0) - dep);
        if (gap >= 60 || toward.length === 1) {
          best = pool[0];
        }
      }
    }
    return best;
  },
  /* A trip's route does not change: the board, its map and the planner
     ask for it once. A failed fetch is asked again. */
  _motisTrips: /* @__PURE__ */ new Map(),
  _motisTripsMax: 32,
  motisTripShape(tripId) {
    const raw = String(tripId || "").replace(/^motis:/i, "").trim();
    if (!raw || /^\d+:/.test(raw)) return Promise.resolve([]);
    const cache = BkkLib._motisTrips;
    const hit = cache.get(raw);
    if (hit) return hit.then((pts) => pts.slice());
    const run = BkkLib.transitousFetch("/api/v5/trip", {
      tripId: raw,
      joinInterlinedLegs: "true"
    }).then((data) => {
      const legs = data && data.legs || [];
      const pts = [];
      legs.forEach((leg) => {
        if (String(leg && leg.mode || "").toUpperCase() === "WALK") return;
        BkkLib.validMapShape(BkkLib.decodePolyline(leg && leg.legGeometry)).forEach((pt) => {
          const last = pts[pts.length - 1];
          if (last && last[0] === pt[0] && last[1] === pt[1]) return;
          pts.push(pt);
        });
      });
      return pts;
    });
    run.catch(() => {
      if (cache.get(raw) === run) cache.delete(raw);
    });
    if (cache.size >= BkkLib._motisTripsMax) cache.delete(cache.keys().next().value);
    cache.set(raw, run);
    return run.then((pts) => pts.slice());
  },
  async motisShapeForHop(row, origin, dest, opts) {
    opts = Object.assign({}, opts || {});
    const anchor = Number(row && (row.dep || row.sched || row.depTs) || 0);
    if (anchor > 0) {
      opts.anchorDep = anchor;
      if (!opts.time) opts.time = new Date((anchor - 120) * 1e3).toISOString();
    }
    const kind = BkkLib.vehicleBucket(row);
    let board = BkkLib.mapGpsLatLon(origin && origin.lat, origin && origin.lon) || BkkLib.knownStopCoords(origin);
    let hit = null;
    let source = "";
    let hopShape = [];
    let fullShape = [];
    try {
      const ref = await BkkLib.transitousStopRef(origin, kind);
      if (ref && BkkLib.mapGpsLatLon(ref.lat, ref.lon)) {
        board = BkkLib.mapGpsLatLon(ref.lat, ref.lon);
      }
      if (ref && ref.id) {
        const data = await BkkLib.transitousFetch("/api/v5/stoptimes", {
          stopId: ref.id,
          time: opts.time || void 0,
          n: "40",
          fetchStops: "true"
        });
        if (!board && data && data.place) {
          board = BkkLib.mapGpsLatLon(data.place.lat, data.place.lon);
        }
        const sts = BkkLib.rowsFromMotisStopTimes(data);
        hit = BkkLib.pickMotisVehicle(sts, row, dest);
        if (hit) source = "stoptimes";
      }
    } catch (_e) {
    }
    if (!hit) {
      const motis = await BkkLib.transitousDepartures(origin, dest, opts);
      hit = BkkLib.pickMotisVehicle(motis, row, dest);
      hopShape = BkkLib.validMapShape(hit && hit.shape);
      if (hit) source = "plan";
    } else {
      hopShape = BkkLib.validMapShape(hit.shape);
    }
    const rawId = BkkLib.motisTripIdRaw(hit);
    if (rawId) {
      try {
        fullShape = await BkkLib.motisTripShape(rawId);
      } catch (_e) {
        fullShape = [];
      }
    }
    let shape = fullShape.length >= 2 ? fullShape : hopShape;
    let destPt = BkkLib.mapGpsLatLon(dest && dest.lat, dest && dest.lon) || BkkLib.knownStopCoords(dest);
    if (!BkkLib.keepsFullLineShape(row)) {
      if (!destPt && dest && (dest.name || dest.key || dest.id)) {
        try {
          const destRef = await BkkLib.transitousStopRef(dest, kind);
          destPt = destRef && BkkLib.mapGpsLatLon(destRef.lat, destRef.lon);
        } catch (_e) {
          destPt = null;
        }
      }
      const clipped = BkkLib.clipShapeToBoardDest(shape, board, destPt);
      if (clipped.length >= 2) shape = clipped;
    }
    return {
      shape,
      board,
      dest: destPt,
      source,
      hit,
      coversHead: BkkLib.motisCoversHead(hit, row)
    };
  },
  /* An empty board (a city line at night) stays empty for a while: after a
     fill that found nothing (or failed), the same pair is not asked again
     for ten minutes, instead of a plan on every poll. */
  _transitousEmptyAt: /* @__PURE__ */ new Map(),
  _transitousEmptyRetryMs: 10 * 60 * 1e3,
  _transitousFillKey(origin, dest, opts) {
    const o = origin || {};
    const d = dest || {};
    return [
      o.id || "",
      o.name || "",
      d.id || "",
      d.key || "",
      d.name || "",
      opts && opts.mode || "all",
      clampMinutesAfter(opts && opts.minutesAfter)
    ].join("|");
  },
  async fillTransitousIfEmpty(rows, origin, dest, opts) {
    const list = Array.isArray(rows) ? rows : [];
    if (list.length) return list;
    const key = BkkLib._transitousFillKey(origin, dest, opts);
    const empties = BkkLib._transitousEmptyAt;
    const now = Date.now();
    if (now - (empties.get(key) || 0) < BkkLib._transitousEmptyRetryMs) return list;
    empties.forEach((ts, k) => {
      if (now - ts >= BkkLib._transitousEmptyRetryMs) empties.delete(k);
    });
    try {
      const extra = await BkkLib.transitousDepartures(origin, dest, opts);
      if (extra.length) empties.delete(key);
      else empties.set(key, Date.now());
      return BkkLib.mergeVolanRows(extra, list, maxDepartureRows(opts && opts.minutesAfter));
    } catch (_e) {
      return list;
    }
  },
  /* MOTIS v5: itinerary duration is seconds; leg duration is seconds;
     startTime/endTime are ISO-8601. */
  journeyFromMotis(payload) {
    const its = payload && payload.itineraries || [];
    if (!its.length) return null;
    return BkkLib.motisJourney(BkkLib.bestItinerary(its, (it) => Date.parse(it.endTime || "")));
  },
  /* Every itinerary of a MOTIS plan, in the order Transitous returns them
     (by departure, or by arrival for arriveBy). */
  journeysFromMotis(payload) {
    const its = payload && payload.itineraries || [];
    return its.map((it) => BkkLib.motisJourney(it)).filter(Boolean);
  },
  motisJourney(best) {
    if (!best) return null;
    const legs = [];
    const tripStart = BkkLib.isoMs(best.startTime);
    let prevEnd = tripStart;
    (best.legs || []).forEach((leg) => {
      const walk = String(leg.mode || "").toUpperCase() === "WALK";
      const startMs = BkkLib.isoMs(leg.startTime);
      const endMs = BkkLib.isoMs(leg.endTime);
      const sec = Number(leg.duration);
      const fromSec = Number.isFinite(sec) && sec > 0 ? sec : startMs && endMs ? Math.round((endMs - startMs) / 1e3) : 0;
      let minutes = fromSec <= 0 ? 0 : Math.max(1, Math.round(fromSec / 60));
      let meters = Math.max(0, Math.round(Number(leg.distance || 0)));
      if (!meters && walk) meters = minutes * 80;
      if (minutes <= 0) {
        if (!walk || meters < 25) return;
        minutes = 1;
      }
      let waitMin2 = 0;
      if (!walk && startMs && prevEnd) {
        waitMin2 = Math.round((startMs - prevEnd) / 6e4);
        if (waitMin2 < 1) waitMin2 = 0;
      }
      if (endMs) prevEnd = endMs;
      else if (startMs) prevEnd = startMs;
      const from = leg.from || {};
      const to = leg.to || {};
      const fromLat = Number(from.lat);
      const fromLon = Number(from.lon);
      const toLat = Number(to.lat);
      const toLon = Number(to.lon);
      const row = {
        walk,
        startMs: startMs || void 0,
        endMs: endMs || void 0,
        minutes,
        meters,
        label: walk ? "" : BkkLib.motisLegLabel(leg),
        headsign: String(leg.headsign || ""),
        color: String(leg.routeColor || "").replace(/#/g, ""),
        text: String(leg.routeTextColor || "").replace(/#/g, ""),
        from: String(from.name || ""),
        to: String(to.name || ""),
        waitMin: waitMin2,
        tripId: String(leg.tripId || ""),
        shape: BkkLib.decodePolyline(leg.legGeometry),
        fromLat: Number.isFinite(fromLat) ? fromLat : void 0,
        fromLon: Number.isFinite(fromLon) ? fromLon : void 0,
        toLat: Number.isFinite(toLat) ? toLat : void 0,
        toLon: Number.isFinite(toLon) ? toLon : void 0
      };
      if (!walk) {
        const badge = BkkLib.applyRailBadge({
          label: row.label,
          color: row.color || "4477AA"
        });
        row.color = String(badge.color || row.color).replace(/#/g, "");
        if (badge.text) row.text = String(badge.text).replace(/#/g, "");
      }
      legs.push(row);
    });
    if (!legs.length) return null;
    const waitMin = legs.reduce((sum, leg) => sum + (leg.waitMin || 0), 0);
    const walkMin = legs.reduce((sum, leg) => sum + (leg.walk ? leg.minutes : 0), 0);
    return {
      durationMin: Math.max(1, Math.round(Number(best.duration || 0) / 60)),
      walkMin,
      waitMin,
      transfers: Math.max(0, Number(best.transfers || 0)),
      startMs: BkkLib.isoMs(best.startTime) || void 0,
      endMs: BkkLib.isoMs(best.endTime) || void 0,
      legs
    };
  },
  /* MOTIS plan between two places. opts.time (epoch ms) plans for that
     moment instead of now; opts.arriveBy makes it the arrival time. */
  async transitousPlan(origin, dest, opts) {
    opts = opts || {};
    const places = await Promise.all([
      BkkLib.transitousPlace(origin),
      BkkLib.transitousPlace(dest)
    ]);
    const fromPlace = places[0];
    const toPlace = places[1];
    if (!fromPlace || !toPlace) return null;
    const planParams = {
      fromPlace,
      toPlace,
      numItineraries: String(opts.numItineraries || 12),
      searchWindow: String(clampMinutesAfter(opts.minutesAfter) * 60)
    };
    if (Number(opts.time) > 0) planParams.time = new Date(Number(opts.time)).toISOString();
    if (opts.arriveBy) planParams.arriveBy = "true";
    return BkkLib.transitousFetch("/api/v5/plan", planParams);
  },
  async transitousJourney(origin, dest, opts) {
    try {
      const data = await BkkLib.transitousPlan(origin, dest, opts);
      const journey = BkkLib.journeyFromMotis(data);
      if (journey && journey.legs.some((leg) => !leg.walk)) return journey;
      return null;
    } catch (_e) {
      return null;
    }
  },
  async transitousJourneys(origin, dest, opts) {
    try {
      const data = await BkkLib.transitousPlan(origin, dest, opts);
      return BkkLib.journeysFromMotis(data).filter((j) => j.legs.some((leg) => !leg.walk));
    } catch (_e) {
      return [];
    }
  },
  /* Direct board rows hide the transfer block unless the itinerary is a
     real transfer that arrives sooner than the fastest listed ride. */
  journeyBeatsBoard(journey, rows) {
    if (!journey || !Array.isArray(journey.legs)) return false;
    const rides = journey.legs.filter((leg) => !leg.walk);
    if (rides.length < 2) return false;
    const dur = Number(journey.durationMin);
    if (!Number.isFinite(dur) || dur <= 0) return false;
    const travels = (Array.isArray(rows) ? rows : []).map((row) => Number(
      row && (row.travelMin != null ? row.travelMin : row.travel)
    )).filter((n) => Number.isFinite(n) && n > 0);
    if (!travels.length) return true;
    return dur < Math.min.apply(null, travels);
  }
});

// src/lib/journey.js
Object.assign(BkkLib, {
  /* FUTÁR plan-trip: itinerary duration and walkTime are seconds.
     Each leg duration is milliseconds. */
  journeyFromPlan(payload) {
    const its = ((((payload || {}).data || {}).entry || {}).plan || {}).itineraries || [];
    if (!its.length) return null;
    return BkkLib.futarJourney(BkkLib.bestItinerary(its, (it) => Number(it.endTime)));
  },
  /* The itinerary that arrives first; among equal arrivals the shorter ride,
     then fewer transfers. The search window spans hours: the shortest
     duration alone picked a trip leaving almost three hours later. */
  bestItinerary(its, endMs) {
    const arrive = (it) => {
      const v = endMs(it);
      return Number.isFinite(v) && v > 0 ? v : Infinity;
    };
    const rides = (it) => (it.legs || []).filter((leg) => {
      const mode = String(leg.mode || "").toUpperCase();
      return mode && mode !== "WALK";
    }).length;
    let best = its[0];
    its.forEach((it) => {
      const a = arrive(it);
      const b = arrive(best);
      if (a !== b) {
        if (a < b) best = it;
        return;
      }
      const da = Number(it.duration || 1e15);
      const db = Number(best.duration || 1e15);
      if (da !== db) {
        if (da < db) best = it;
        return;
      }
      if (rides(it) < rides(best)) best = it;
    });
    return best;
  },
  journeysFromPlan(payload) {
    const its = ((((payload || {}).data || {}).entry || {}).plan || {}).itineraries || [];
    return its.map((it) => BkkLib.futarJourney(it)).filter(Boolean);
  },
  futarJourney(best) {
    if (!best) return null;
    const legs = [];
    const tripStart = BkkLib.epochMs(best.startTime);
    let prevEnd = tripStart;
    (best.legs || []).forEach((leg) => {
      const sec = Math.round(Number(leg.duration || 0) / 1e3);
      const walk = String(leg.mode || "").toUpperCase() === "WALK";
      const startMs = BkkLib.epochMs(leg.startTime);
      const endMs = BkkLib.epochMs(leg.endTime);
      let waitMin2 = 0;
      if (!walk && startMs && prevEnd) {
        waitMin2 = Math.round((startMs - prevEnd) / 6e4);
        if (waitMin2 < 1) waitMin2 = 0;
      }
      if (endMs) prevEnd = endMs;
      else if (startMs) prevEnd = startMs;
      let minutes = sec <= 0 ? 0 : Math.max(1, Math.round(sec / 60));
      let meters = Math.max(0, Math.round(Number(leg.distance || 0)));
      if (!meters && walk) meters = minutes * 80;
      if (minutes <= 0) {
        if (!walk || meters < 25) return;
        minutes = 1;
      }
      const from = leg.from || {};
      const to = leg.to || {};
      const fromLat = Number(from.lat);
      const fromLon = Number(from.lon);
      const toLat = Number(to.lat);
      const toLon = Number(to.lon);
      legs.push({
        walk,
        startMs: startMs || void 0,
        endMs: endMs || void 0,
        minutes,
        meters,
        label: String(leg.routeShortName || ""),
        headsign: String(leg.headsign || ""),
        color: String(leg.routeColor || "").replace(/#/g, ""),
        text: String(leg.routeTextColor || "").replace(/#/g, ""),
        from: String(from.name || ""),
        to: String(to.name || ""),
        waitMin: waitMin2,
        tripId: String(leg.tripId || ""),
        shape: BkkLib.decodePolyline(leg.legGeometry),
        fromLat: Number.isFinite(fromLat) ? fromLat : void 0,
        fromLon: Number.isFinite(fromLon) ? fromLon : void 0,
        toLat: Number.isFinite(toLat) ? toLat : void 0,
        toLon: Number.isFinite(toLon) ? toLon : void 0
      });
    });
    if (!legs.length) return null;
    const waitMin = legs.reduce((sum, leg) => sum + (leg.waitMin || 0), 0);
    return {
      durationMin: Math.max(1, Math.round(Number(best.duration || 0) / 60)),
      walkMin: Math.max(0, Math.round(Number(best.walkTime || 0) / 60)),
      waitMin,
      transfers: Math.max(0, Number(best.transfers || 0)),
      startMs: BkkLib.epochMs(best.startTime) || void 0,
      endMs: BkkLib.epochMs(best.endTime) || void 0,
      legs
    };
  },
  /* MOTIS and FUTÁR name coordinate endpoints START and END. */
  nameJourneyEnds(journey, origin, dest) {
    if (!journey || !Array.isArray(journey.legs)) return journey;
    const from = String(origin && origin.name || "").trim();
    const to = String(dest && dest.name || "").trim();
    journey.legs.forEach((leg) => {
      if (from && /^start$/i.test(String(leg.from || "").trim())) leg.from = from;
      if (to && /^end$/i.test(String(leg.to || "").trim())) leg.to = to;
    });
    return journey;
  },
  /* FUTÁR plan-trip parameters for a chosen moment (Budapest wall clock). */
  futarPlanTime(opts) {
    const ms = Number(opts && opts.time);
    if (!(ms > 0)) return {};
    const f = tzFields(new Date(ms));
    const out = {
      date: `${f.y}-${pad2(f.mo)}-${pad2(f.d)}`,
      time: `${pad2(f.h)}:${pad2(f.mi)}`
    };
    if (opts.arriveBy) out.arriveBy = "true";
    return out;
  },
  /* Keep only itineraries nobody beats: drop one when another leaves no
     earlier and arrives no later (a 22:39 → 06:52 night wait next to a
     01:56 → 04:33). Ties keep the one with fewer transfers. */
  dropDominatedJourneys(list) {
    const items = (Array.isArray(list) ? list : []).filter(Boolean);
    return items.filter((a) => {
      if (!a.startMs || !a.endMs) return true;
      return !items.some((b) => {
        if (b === a || !b.startMs || !b.endMs) return false;
        if (b.startMs < a.startMs || b.endMs > a.endMs) return false;
        if (b.startMs > a.startMs || b.endMs < a.endMs) return true;
        return (b.transfers || 0) < (a.transfers || 0);
      });
    });
  },
  /* Several itineraries for the planner's chosen time: Transitous first,
     FUTÁR plan-trip when it has none. */
  async planJourneys(apiKey, origin, dest, hass, opts) {
    opts = opts || {};
    [origin, dest] = await this.withGtfsCoords([origin, dest]);
    let list = await BkkLib.transitousJourneys(origin, dest, opts);
    if (!list.length && BkkLib.hasFutar(apiKey)) {
      const [fromPlace, toPlace] = await Promise.all([
        BkkLib.planPlace(apiKey, origin),
        BkkLib.planPlace(apiKey, dest)
      ]);
      if (fromPlace && toPlace) {
        try {
          const data = await BkkLib.fetch(apiKey, "plan-trip.json", Object.assign({
            fromPlace,
            toPlace,
            mode: "TRANSIT,WALK",
            numItineraries: "6"
          }, BkkLib.futarPlanTime(opts)), true);
          list = BkkLib.journeysFromPlan(data).filter((j) => j.legs.some((leg) => !leg.walk));
        } catch (_e) {
          list = [];
        }
      }
    }
    list.forEach((j) => BkkLib.nameJourneyEnds(j, origin, dest));
    list = BkkLib.dropDominatedJourneys(list);
    const at = Number(opts.time) || Date.now();
    const key = opts.arriveBy ? "endMs" : "startMs";
    list.sort((a, b) => (a[key] || at) - (b[key] || at));
    if (opts.arriveBy) {
      list.reverse();
    }
    return list.slice(0, opts.max || 6);
  },
  async planJourney(apiKey, origin, dest, hass, opts) {
    opts = opts || {};
    [origin, dest] = await this.withGtfsCoords([origin, dest]);
    const motis = await BkkLib.transitousJourney(origin, dest, opts);
    if (motis && motis.legs.some((leg) => !leg.walk)) return BkkLib.nameJourneyEnds(motis, origin, dest);
    let futar = null;
    if (BkkLib.hasFutar(apiKey)) {
      const [fromPlace, toPlace] = await Promise.all([
        BkkLib.planPlace(apiKey, origin),
        BkkLib.planPlace(apiKey, dest)
      ]);
      if (fromPlace && toPlace) {
        try {
          const horizon = clampMinutesAfter(opts.minutesAfter);
          const data = await BkkLib.fetch(apiKey, "plan-trip.json", {
            fromPlace,
            toPlace,
            mode: "TRANSIT,WALK",
            numItineraries: String(Math.min(12, Math.max(5, Math.round(horizon / 40)))),
            searchWindow: String(horizon * 60)
          }, true);
          futar = BkkLib.journeyFromPlan(data);
        } catch (_e) {
          futar = null;
        }
      }
    }
    if (futar && futar.legs.some((leg) => !leg.walk)) return BkkLib.nameJourneyEnds(futar, origin, dest);
    const viaCity = await this.cityRailJourney(apiKey, hass, origin, dest);
    return viaCity || futar;
  },
  /* One operator's ride and walk edges for a service day: the fastest
     ride between any two stops of a trip (up to 28 stops in), with every
     route that serves the pair, and walks between same-named stops. Built
     once per day, not per question. */
  _cityHubGraphs: /* @__PURE__ */ new WeakMap(),
  _cityHubGraph(idx, op, ymd) {
    let byIdx = BkkLib._cityHubGraphs.get(idx);
    if (!byIdx) {
      byIdx = /* @__PURE__ */ new Map();
      BkkLib._cityHubGraphs.set(idx, byIdx);
    }
    const memoKey = op + "|" + ymd;
    const hit = byIdx.get(memoKey);
    if (hit) return hit;
    const stops = idx.stops || [];
    const dow = tzDow(ymd);
    const ride = /* @__PURE__ */ new Map();
    const trips = idx.t || [];
    for (let n = 0; n < trips.length; n++) {
      const t2 = trips[n];
      const ps = t2[2] || [];
      const ms = t2[3] || [];
      if (ps.length < 2) continue;
      const first = stops[ps[0]];
      if (!first || first.op !== op) continue;
      if (!BkkLib.volanServiceOk(idx, t2[0], ymd, dow)) continue;
      const span = Math.min(ps.length, 28);
      for (let i = 0; i < span; i++) {
        for (let j = i + 1; j < span; j++) {
          const mins = ms[j] - ms[i];
          if (mins < 1 || mins > 150) continue;
          const key = ps[i] + "|" + ps[j];
          const route = String(t2[1]);
          const prev = ride.get(key);
          if (!prev) {
            ride.set(key, { from: ps[i], to: ps[j], route, routes: [route], ride: mins });
            continue;
          }
          if (prev.routes.indexOf(route) < 0) prev.routes.push(route);
          if (mins < prev.ride) {
            prev.ride = mins;
            prev.route = route;
          }
        }
      }
    }
    const rideAt = /* @__PURE__ */ new Map();
    ride.forEach((edge) => {
      if (!rideAt.has(edge.from)) rideAt.set(edge.from, []);
      rideAt.get(edge.from).push(edge);
    });
    const walkAt = /* @__PURE__ */ new Map();
    const byFold = /* @__PURE__ */ new Map();
    stops.forEach((st) => {
      if (!st || st.op !== op || !st.fold) return;
      if (!byFold.has(st.fold)) byFold.set(st.fold, []);
      byFold.get(st.fold).push(st.i);
    });
    byFold.forEach((group) => {
      if (group.length < 2 || group.length > 8) return;
      group.forEach((from) => {
        group.forEach((to) => {
          if (from === to) return;
          if (!walkAt.has(from)) walkAt.set(from, []);
          walkAt.get(from).push(to);
        });
      });
    });
    const graph = { rideAt, walkAt };
    Array.from(byIdx.keys()).forEach((k) => {
      if (k.split("|")[1] !== ymd) byIdx.delete(k);
    });
    byIdx.set(memoKey, graph);
    return graph;
  },
  /* Local GTFS has no planner API. Ride the city feed to a station, walk
     across, then take the next train. Same-name stops are a 3 minute walk;
     the station bay to the platform is 6 minutes at 80 m/min. The paths
     come cheapest first by ride time alone, at most `perHub` to each
     station, so the nearest one's variants do not crowd out the others;
     the timetable picks among them. */
  cityHubPaths(idx, originStop, perHub = 6) {
    if (!idx || !originStop) return [];
    const stops = idx.stops || [];
    const op = originStop.op;
    const { rideAt, walkAt } = BkkLib._cityHubGraph(idx, op, BkkLib.volanDay(0).ymd);
    const hub = (st) => BkkLib.isRailHubName(st && st.name);
    const starts = [];
    stops.forEach((st) => {
      if (st && st.op === op && st.fold === originStop.fold) starts.push(st.i);
    });
    if (!starts.length) starts.push(originStop.i);
    const board = (at) => {
      const ids = [at];
      (walkAt.get(at) || []).forEach((to) => {
        if (ids.indexOf(to) < 0) ids.push(to);
      });
      return ids;
    };
    const hubs = [];
    const consider = (state) => {
      const here = stops[state.at];
      if (hub(here) && state.cost > 0) hubs.push(state);
    };
    starts.forEach((startAt) => {
      board(startAt).forEach((bay) => {
        const walked = bay === startAt ? 0 : 3;
        const base = {
          at: bay,
          rides: 0,
          cost: walked,
          prev: walked ? {
            at: startAt,
            prev: null,
            walk: true,
            minutes: 3,
            from: stops[startAt] && stops[startAt].name,
            to: stops[bay] && stops[bay].name
          } : null
        };
        (rideAt.get(bay) || []).forEach((edge) => {
          const first = {
            at: edge.to,
            rides: 1,
            cost: base.cost + edge.ride,
            prev: base.prev || base,
            walk: false,
            route: edge.route,
            routes: edge.routes,
            minutes: edge.ride,
            fromI: edge.from,
            toI: edge.to,
            from: stops[bay] && stops[bay].name,
            to: stops[edge.to] && stops[edge.to].name
          };
          consider(first);
          board(edge.to).forEach((mid) => {
            const midWalk = mid === edge.to ? 0 : 3;
            (rideAt.get(mid) || []).forEach((edge2) => {
              if (edge2.to === edge.to) return;
              const secondPrev = {
                at: mid,
                rides: 1,
                cost: first.cost + midWalk,
                prev: first,
                walk: midWalk > 0,
                minutes: midWalk,
                fromI: edge.to,
                toI: mid,
                from: stops[edge.to] && stops[edge.to].name,
                to: stops[mid] && stops[mid].name
              };
              const second = {
                at: edge2.to,
                rides: 2,
                cost: first.cost + midWalk + edge2.ride,
                prev: midWalk ? secondPrev : first,
                walk: false,
                route: edge2.route,
                routes: edge2.routes,
                minutes: edge2.ride,
                fromI: edge2.from,
                toI: edge2.to,
                from: stops[mid] && stops[mid].name,
                to: stops[edge2.to] && stops[edge2.to].name
              };
              consider(second);
            });
          });
        });
      });
    });
    const perStation = /* @__PURE__ */ new Map();
    const kept = hubs.sort((a, b) => a.cost - b.cost).filter((state) => {
      const st = stops[state.at];
      const key = st && (st.fold || st.name) || String(state.at);
      const n = perStation.get(key) || 0;
      perStation.set(key, n + 1);
      return n < perHub;
    });
    return kept.map((state) => {
      const edges = [];
      for (let step = state; step && step.prev; step = step.prev) {
        edges.push({
          walk: !!step.walk,
          route: step.route || "",
          routes: step.walk ? [] : (step.routes || [step.route || ""]).slice(),
          minutes: step.minutes,
          fromI: step.fromI,
          toI: step.toI,
          from: step.from || "",
          to: step.to || ""
        });
      }
      edges.reverse();
      const hubStop = stops[state.at];
      const hubPt = hubStop ? BkkLib.finiteLatLon(hubStop.lat, hubStop.lon) : null;
      return {
        minutes: state.cost,
        hubName: hubStop && hubStop.name,
        hubFold: hubStop && hubStop.fold,
        hubLat: hubPt ? hubPt[0] : void 0,
        hubLon: hubPt ? hubPt[1] : void 0,
        edges
      };
    });
  },
  cityHubPath(idx, originStop) {
    return BkkLib.cityHubPaths(idx, originStop, 1)[0] || null;
  },
  /* Only from a city feed's own stop: a BKK or Volán origin matched by
     number or name lands on some other town's stop (BKK_F01020 "Kossuth
     Lajos utca" was a Miskolc one). */
  async cityRailJourney(apiKey, hass, origin, dest) {
    if (!BkkLib.isRailDest(dest) || BkkLib.stopIdFamily(origin) !== "city") return null;
    let idx;
    try {
      idx = await this.cityIndex();
    } catch (_e) {
      return null;
    }
    if (!idx || !idx.stops) return null;
    let originStop = BkkLib.gtfsStopById(idx, origin.id);
    if (!originStop) {
      const opIx = BkkLib.opIx(idx, String(origin.id).split(":")[0]);
      if (opIx >= 0) originStop = BkkLib.volanMatchStop(idx, { id: "", name: origin.name || "" }, opIx);
    }
    if (!originStop || originStop.op == null) return null;
    const paths = BkkLib.cityHubPaths(idx, originStop).filter((p) => p.edges.length);
    if (!paths.length) return null;
    const op = (idx.ops || [])[originStop.op];
    const cityName = op ? String(op.name).split(" — ")[0] : "";
    const destFold = BkkLib.fold(dest && dest.name || "");
    const cityTok = new Set(BkkLib.foldTokens(cityName));
    const destTok = BkkLib.foldTokens(destFold);
    const destPt = BkkLib.finiteLatLon(dest && dest.lat, dest && dest.lon);
    const isDestHub = (path) => {
      const hubTok = BkkLib.foldTokens(path.hubFold);
      const hubPt = BkkLib.finiteLatLon(path.hubLat, path.hubLon);
      return !!(destFold && path.hubFold && (destFold === path.hubFold || hubPt && destPt && BkkLib.haversineMeters(hubPt, destPt) <= 800 || destTok.length && destTok.every((tok) => hubTok.indexOf(tok) >= 0 || cityTok.has(tok))));
    };
    const toDest = paths.filter(isDestHub);
    const destIsHub = toDest.length > 0;
    const startSec = Math.floor(Date.now() / 1e3);
    const rides = /* @__PURE__ */ new Map();
    const nextRide = (edge, readySec) => {
      const key = edge.fromI + "|" + edge.toI + "|" + edge.routes.join(",") + "|" + readySec;
      if (!rides.has(key)) rides.set(key, BkkLib.nextCityRide(idx, edge.fromI, edge.toI, edge.routes, readySec));
      return rides.get(key);
    };
    const timeCityPath = (path) => {
      let ready2 = startSec;
      const legs2 = [];
      for (let i = 0; i < path.edges.length; i++) {
        const edge = path.edges[i];
        if (edge.walk) {
          const startMs2 = ready2 * 1e3;
          ready2 += edge.minutes * 60;
          legs2.push({
            walk: true,
            minutes: edge.minutes,
            meters: edge.minutes * 80,
            label: "",
            headsign: "",
            color: "",
            text: "",
            from: edge.from,
            to: edge.to,
            waitMin: 0,
            startMs: startMs2,
            endMs: ready2 * 1e3,
            fromI: edge.fromI,
            toI: edge.toI
          });
          continue;
        }
        const timed = nextRide(edge, ready2);
        if (!timed) return null;
        const waitMin2 = Math.round((timed.dep - ready2) / 60);
        const tripStops = timed.trip && timed.trip[2] || [];
        const terminus = idx.stops[tripStops[tripStops.length - 1]];
        legs2.push({
          walk: false,
          minutes: Math.max(1, Math.round((timed.arr - timed.dep) / 60)),
          meters: 0,
          label: timed.trip ? String(timed.trip[1]) : edge.route,
          headsign: terminus && terminus.name || edge.to,
          color: "0F6FC6",
          text: "FFFFFF",
          from: edge.from,
          to: edge.to,
          waitMin: waitMin2 >= 1 ? waitMin2 : 0,
          startMs: timed.dep * 1e3,
          endMs: timed.arr * 1e3,
          /* The trip's stops and shapes.txt route, so the map draws the ride
             along its streets instead of a line between two names. */
          gtfs: timed.trip ? BkkLib.gtfsTripGeo(idx, timed.trip, timed.o, timed.d) : null,
          fromI: edge.fromI,
          toI: edge.toI
        });
        ready2 = timed.arr;
      }
      return { path, legs: legs2, ready: ready2 };
    };
    const bestAt = /* @__PURE__ */ new Map();
    const tried = destIsHub ? toDest : paths;
    let first = null;
    for (let i = 0; i < tried.length; i++) {
      const floor = startSec + tried[i].minutes * 60;
      if (destIsHub && first && floor >= first.ready) break;
      const key = tried[i].hubFold || tried[i].hubName || "";
      const held = bestAt.get(key);
      if (held && floor >= held.ready) continue;
      const timed = timeCityPath(tried[i]);
      if (!timed) continue;
      if (!held || timed.ready < held.ready) bestAt.set(key, timed);
      if (!first || timed.ready < first.ready) first = timed;
    }
    if (!first) return null;
    let best = first;
    if (!destIsHub) {
      const hubs = Array.from(bestAt.values()).sort((a, b) => a.ready - b.ready).slice(0, 3);
      const rails = await Promise.all(hubs.map((city) => this.railAfterCity(apiKey, hass, cityName, city.path, dest, city.ready).catch(() => null)));
      best = null;
      let railBest = null;
      rails.forEach((rail, n) => {
        if (rail && (!railBest || rail.ready < railBest.ready)) {
          railBest = rail;
          best = hubs[n];
        }
      });
      if (!best) return null;
      best.legs.push(railBest.walk);
      best.legs.push(railBest.train);
      best.ready = railBest.ready;
    }
    const { legs, ready } = best;
    const firstRide = legs.findIndex((leg) => !leg.walk);
    if (firstRide >= 0) {
      let at = legs[firstRide].startMs;
      for (let i = firstRide - 1; i >= 0; i--) {
        legs[i].endMs = at;
        legs[i].startMs = at - legs[i].minutes * 6e4;
        at = legs[i].startMs;
      }
      legs[firstRide].waitMin = 0;
    }
    const waitMin = legs.reduce((sum, leg) => sum + (leg.waitMin || 0), 0);
    const walkMin = legs.reduce((sum, leg) => sum + (leg.walk ? leg.minutes : 0), 0);
    const start = BkkLib.finiteLatLon(origin && origin.lat, origin && origin.lon);
    const end = BkkLib.finiteLatLon(dest && dest.lat, dest && dest.lon);
    if (start && legs[0] && legs[0].fromLat == null) {
      legs[0].fromLat = start[0];
      legs[0].fromLon = start[1];
    }
    if (end && legs.length) {
      const last = legs[legs.length - 1];
      if (last.toLat == null) {
        last.toLat = end[0];
        last.toLon = end[1];
      }
    }
    const stopAt = (i) => {
      const st = Number.isInteger(i) ? idx.stops[i] : null;
      return st ? BkkLib.finiteLatLon(st.lat, st.lon) : null;
    };
    legs.forEach((leg) => {
      const a = stopAt(leg.fromI);
      const b = stopAt(leg.toI);
      if (a && leg.fromLat == null) {
        leg.fromLat = a[0];
        leg.fromLon = a[1];
      }
      if (b && leg.toLat == null) {
        leg.toLat = b[0];
        leg.toLon = b[1];
      }
      delete leg.fromI;
      delete leg.toI;
    });
    const startMs = legs[0] && legs[0].startMs || startSec * 1e3;
    const endMs = ready * 1e3;
    return {
      durationMin: Math.max(1, Math.round((endMs - startMs) / 6e4)),
      walkMin,
      waitMin,
      transfers: Math.max(0, legs.filter((leg) => !leg.walk).length - 1),
      startMs,
      endMs,
      legs
    };
  },
  /* The ride that gets there first on any of `routes` (one route or a
     list): a slow line leaving now beats a fast one leaving at dawn. */
  /* An index's trips by route, so a ride is looked up among its own
     line's trips, not the whole feed's, for every path and day. */
  _cityTripsByRoute: /* @__PURE__ */ new WeakMap(),
  cityTripsOf(idx, routes) {
    let byRoute = BkkLib._cityTripsByRoute.get(idx);
    if (!byRoute) {
      byRoute = /* @__PURE__ */ new Map();
      (idx.t || []).forEach((t2) => {
        const key = String(t2[1]);
        if (!byRoute.has(key)) byRoute.set(key, []);
        byRoute.get(key).push(t2);
      });
      BkkLib._cityTripsByRoute.set(idx, byRoute);
    }
    const out = [];
    new Set([].concat(routes).map(String)).forEach((route) => {
      (byRoute.get(route) || []).forEach((t2) => out.push(t2));
    });
    return out;
  },
  nextCityRide(idx, fromI, toI, routes, readySec) {
    const trips = BkkLib.cityTripsOf(idx, routes);
    let best = null;
    for (let off = -1; off < 2; off++) {
      const day = BkkLib.volanDay(off);
      for (let n = 0; n < trips.length; n++) {
        const t2 = trips[n];
        if (!BkkLib.volanServiceOk(idx, t2[0], day.ymd, day.dow)) continue;
        const ps = t2[2] || [];
        const ms = t2[3] || [];
        const o = ps.indexOf(fromI);
        const d = ps.indexOf(toI);
        if (o < 0 || d <= o) continue;
        const dep = day.base + ms[o] * 60;
        if (dep < readySec - 30) continue;
        const arr = day.base + ms[d] * 60;
        if (!best || arr < best.arr || arr === best.arr && dep > best.dep) best = { dep, arr, trip: t2, o, d };
      }
    }
    return best;
  },
  /* The railway station at a city feed's hub, from FUTÁR's MÁV stops: the
     one by the hub stop, else by name. Hubs called just "Vasútállomás"
     (Székesfehérvár, Szombathely, Kaposvár …) or "Főpályaudvar" (Pécs)
     share no word with the station ("Székesfehérvár", "Pécs"), so the town
     names it then; a town with two stations goes by the hub's own word
     (Miskolc "Gömöri pályaudvar" is Miskolc-Gömöri, not Miskolc-Tiszai). */
  async railStationAtHub(apiKey, cityName, path) {
    const hubPt = BkkLib.finiteLatLon(path.hubLat, path.hubLon);
    const want = path.hubFold || path.hubName || "";
    const cityTok = BkkLib.foldTokens(cityName);
    const queries = [(cityName ? cityName + " " : "") + (path.hubName || "")];
    if (cityName && queries.indexOf(cityName) < 0) queries.push(cityName);
    for (let i = 0; i < queries.length; i++) {
      let hits = [];
      try {
        hits = await this.searchStops(apiKey, queries[i], "mav") || [];
      } catch (_e) {
        hits = [];
      }
      const near = hubPt ? hits.map((hit) => ({ hit, pt: BkkLib.finiteLatLon(hit.lat, hit.lon) })).filter((c) => c.pt && BkkLib.haversineMeters(hubPt, c.pt) <= 1500).sort((x, y) => BkkLib.haversineMeters(hubPt, x.pt) - BkkLib.haversineMeters(hubPt, y.pt))[0] : null;
      if (near) return near.hit;
      const byHub = hits.find((hit) => BkkLib.foldTokenMatch(want, hit.name || ""));
      if (byHub) return byHub;
      const extra = (hit) => BkkLib.foldTokens(hit.name || "").filter((tok) => cityTok.indexOf(tok) < 0).length;
      const byTown = cityTok.length ? hits.filter((hit) => BkkLib.foldTokens(hit.name || "").some((tok) => cityTok.indexOf(tok) >= 0)).sort((x, y) => extra(x) - extra(y))[0] : null;
      if (byTown) return byTown;
    }
    return null;
  },
  async railAfterCity(apiKey, hass, cityName, path, dest, readySec) {
    if (!path || !path.hubName) return null;
    const station = await this.railStationAtHub(apiKey, cityName, path);
    if (!station || !station.id) return null;
    const walkStart = readySec;
    const walkEnd = readySec + 6 * 60;
    const walk = {
      walk: true,
      stationWalk: true,
      minutes: 6,
      meters: 480,
      label: "",
      headsign: "",
      color: "",
      text: "",
      from: path.hubName || "",
      to: station.name || "",
      waitMin: 0,
      startMs: walkStart * 1e3,
      endMs: walkEnd * 1e3
    };
    const stationPt = BkkLib.finiteLatLon(station.lat, station.lon);
    if (stationPt) {
      walk.toLat = stationPt[0];
      walk.toLon = stationPt[1];
    }
    let train = null;
    if (hass && dest && dest.id) {
      try {
        const destCode = await this.elviraResolveCode(apiKey, dest.id, dest.name || "");
        const rows = destCode ? await this.elviraBetween(
          hass,
          station.id,
          destCode,
          480,
          "",
          { apiKey, originName: station.name || "" }
        ) : [];
        const next = (rows || []).filter((row) => row.destHit !== false && Number(row.dep) >= walkEnd - 30).sort((a, b) => a.dep - b.dep)[0];
        if (next && next.dep) {
          const rideMin = Number(next.travel) > 0 ? Number(next.travel) : 0;
          const arr = rideMin ? next.dep + rideMin * 60 : next.dep;
          const waitMin = Math.round((next.dep - walkEnd) / 60);
          const badge = BkkLib.applyRailBadge({
            label: String(next.label || next.trainNumber || ""),
            color: "4477AA"
          });
          train = {
            walk: false,
            minutes: rideMin || 1,
            meters: 0,
            label: String(next.label || next.trainNumber || ""),
            headsign: String(next.head || dest.name || ""),
            color: String(badge.color || "4477AA").replace("#", ""),
            text: String(badge.text || "FFFFFF").replace("#", ""),
            from: station.name || "",
            to: dest.name || "",
            waitMin: waitMin >= 1 ? waitMin : 0,
            startMs: next.dep * 1e3,
            endMs: arr * 1e3
          };
          if (stationPt) {
            train.fromLat = stationPt[0];
            train.fromLon = stationPt[1];
          }
        }
      } catch (_e) {
      }
    }
    if (!train) return null;
    return { walk, train, ready: Math.round(train.endMs / 1e3) };
  },
  async probeApiKey(apiKey) {
    if (!BkkLib.hasFutar(apiKey)) return { ok: false, code: "errNoApiKey" };
    try {
      await BkkLib.fetch(apiKey, "search.json", { query: "." }, true);
      return { ok: true };
    } catch (err) {
      const msg = String(err && err.message ? err.message : err);
      if (err && err.code === "futar_auth" || /HTTP 401|HTTP 403/.test(msg)) {
        return { ok: false, code: "errApiKeyBad" };
      }
      if (err && err.i18nKey === "errNoApiKey") return { ok: false, code: "errNoApiKey" };
      return { ok: false, code: "errApiKeyProbe" };
    }
  }
});

// src/lib/geo.js
var SHAPE_LOOP_CLOSE_M = 100;
var SHAPE_LOOP_RATIO = 25;
var SHAPE_PASS_M = 80;
var SHAPE_PASS_WEIGHT = 4;
Object.assign(BkkLib, {
  hm(ts) {
    if (!ts) return "";
    const f = tzFields(new Date(ts * 1e3));
    return `${pad2(f.h)}:${pad2(f.mi)}`;
  },
  vehicleKind(rt) {
    if (BkkLib.isMavRoute(rt)) return "rail";
    const t2 = String(rt && rt.type || "").toUpperCase();
    if (t2.indexOf("SUBURBAN") >= 0) return "rail";
    if (t2.indexOf("TRAM") >= 0) return "tram";
    if (t2.indexOf("SUBWAY") >= 0 || t2.indexOf("METRO") >= 0) return "subway";
    if (t2.indexOf("TROLLEY") >= 0) return "trolleybus";
    if (t2 === "COACH" || BkkLib.isVolanRoute(rt)) return "coach";
    return "bus";
  },
  vehicleLoc(veh) {
    if (!veh || typeof veh !== "object") return { lat: NaN, lon: NaN };
    const loc = veh.location || {};
    const lat = loc.lat != null ? loc.lat : veh.latitude;
    const lon = loc.lon != null ? loc.lon : veh.longitude != null ? veh.longitude : veh.lng;
    const pt = BkkLib.mapGpsLatLon(lat, lon);
    if (!pt) return { lat: NaN, lon: NaN };
    return { lat: pt[0], lon: pt[1] };
  },
  unixSec(ts) {
    const n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return Date.now() / 1e3;
    return n > 1e12 ? n / 1e3 : n;
  },
  decodePolyline(encoded) {
    let precision = 5;
    let fromObject = false;
    let hadPrecision = false;
    if (encoded && typeof encoded === "object") {
      fromObject = true;
      const p = Number(encoded.precision);
      if (Number.isFinite(p) && p >= 4 && p <= 7) {
        precision = p;
        hadPrecision = true;
      }
      encoded = encoded.points || "";
    }
    if (!encoded || typeof encoded !== "string") return [];
    const factor = Math.pow(10, precision);
    const coords = [];
    let index = 0;
    let lat = 0;
    let lng = 0;
    try {
      while (index < encoded.length) {
        for (let xy = 0; xy < 2; xy++) {
          let result2 = 0;
          let shift = 0;
          let byte;
          do {
            byte = encoded.charCodeAt(index++) - 63;
            result2 |= (byte & 31) << shift;
            shift += 5;
          } while (byte >= 32);
          const delta = result2 & 1 ? ~(result2 >> 1) : result2 >> 1;
          if (xy === 0) lat += delta;
          else lng += delta;
        }
        coords.push([lat / factor, lng / factor]);
      }
    } catch (_e) {
      return [];
    }
    let result = coords;
    if (coords.length && precision === 5 && (Math.abs(coords[0][0]) > 90 || Math.abs(coords[0][1]) > 180)) {
      if (!fromObject || hadPrecision) {
        return BkkLib.decodePolyline({ points: encoded, precision: 6 });
      }
      result = [];
    } else if (coords.length && (Math.abs(coords[0][0]) > 90 || Math.abs(coords[0][1]) > 180)) {
      result = [];
    } else if (coords.length > 2500) {
      const out = [];
      const step = (coords.length - 1) / 2499;
      for (let i = 0; i < 2500; i++) {
        const idx = Math.round(i * step);
        const pt = coords[Math.min(idx, coords.length - 1)];
        if (!out.length || out[out.length - 1][0] !== pt[0] || out[out.length - 1][1] !== pt[1]) {
          out.push(pt);
        }
      }
      const last = coords[coords.length - 1];
      if (out[out.length - 1][0] !== last[0] || out[out.length - 1][1] !== last[1]) out.push(last);
      result = out;
    }
    return result;
  },
  shapeSpanMeters(shape) {
    const pts = BkkLib.validMapShape(shape);
    if (pts.length < 2) return 0;
    let minLat = 90;
    let maxLat = -90;
    let minLon = 180;
    let maxLon = -180;
    pts.forEach((p) => {
      if (p[0] < minLat) minLat = p[0];
      if (p[0] > maxLat) maxLat = p[0];
      if (p[1] < minLon) minLon = p[1];
      if (p[1] > maxLon) maxLon = p[1];
    });
    return BkkLib.haversineMeters([minLat, minLon], [maxLat, maxLon]);
  },
  pickFullerShape(a, b) {
    const sa = BkkLib.validMapShape(a);
    const sb = BkkLib.validMapShape(b);
    if (sa.length < 2) return sb;
    if (sb.length < 2) return sa;
    return BkkLib.shapeSpanMeters(sb) > BkkLib.shapeSpanMeters(sa) ? sb : sa;
  },
  stitchShapes(primary, extra) {
    const a = BkkLib.validMapShape(primary);
    const b = BkkLib.validMapShape(extra);
    if (a.length < 2) return b;
    if (b.length < 2) return a;
    const spanA = BkkLib.shapeSpanMeters(a);
    const spanB = BkkLib.shapeSpanMeters(b);
    if (spanB <= spanA + 1500) return spanB > spanA ? b : a;
    const end = a[a.length - 1];
    let best = 0;
    let bestD = 1e15;
    b.forEach((p, i) => {
      const d = BkkLib.haversineMeters(p, end);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    });
    if (bestD > 3e3) return spanB > spanA ? b : a;
    const tail = b.slice(best);
    if (tail.length < 2) return spanB > spanA ? b : a;
    return a.concat(tail.slice(1));
  },
  /* Metres from a point to the nearest segment of a route (not just its
     vertices, which sit kilometres apart on straight railway chords). */
  distanceToShapeMeters(pt, shape) {
    const p = Array.isArray(pt) ? BkkLib.mapGpsLatLon(pt[0], pt[1]) : null;
    const pts = BkkLib.validMapShape(shape);
    if (!p || !pts.length) return null;
    const k = Math.cos(p[0] * Math.PI / 180);
    const xy = (q) => [(q[1] - p[1]) * k * 111320, (q[0] - p[0]) * 110540];
    if (pts.length === 1) return Math.hypot(...xy(pts[0]));
    let best = Infinity;
    for (let i = 1; i < pts.length; i++) {
      const a = xy(pts[i - 1]);
      const b = xy(pts[i]);
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      const len = dx * dx + dy * dy;
      const t2 = len ? Math.max(0, Math.min(1, -(a[0] * dx + a[1] * dy) / len)) : 0;
      const d = Math.hypot(a[0] + t2 * dx, a[1] + t2 * dy);
      if (d < best) best = d;
    }
    return best;
  },
  /* A stop pin belongs on the drawn route. When the chosen point is off it
     (a hand-kept or configured coordinate) and another source puts the stop
     on the route, that one wins. Without a better candidate the chosen
     point stays: moving it along the line would only guess. */
  pinOnRoute(chosen, candidates, shape) {
    const asPt = (q) => Array.isArray(q) ? BkkLib.mapGpsLatLon(q[0], q[1]) : null;
    const first = asPt(chosen);
    const others = (candidates || []).map(asPt).filter(Boolean);
    if (BkkLib.validMapShape(shape).length < 2) return first || others[0] || null;
    const gap = (q) => BkkLib.distanceToShapeMeters(q, shape);
    if (first && gap(first) <= 150) return first;
    let best = null;
    let bestGap = Infinity;
    others.forEach((q) => {
      const g = gap(q);
      if (g < bestGap) {
        best = q;
        bestGap = g;
      }
    });
    if (best && bestGap <= 60) return best;
    return first || best;
  },
  shapeGpsGapMeters(shape, lat, lon) {
    const gps = BkkLib.mapGpsLatLon(lat, lon);
    const pts = BkkLib.validMapShape(shape);
    if (!gps || pts.length < 1) return null;
    let best = Infinity;
    for (let i = 0; i < pts.length; i++) {
      const d = BkkLib.haversineMeters(gps, pts[i]);
      if (d < best) best = d;
    }
    return best;
  },
  combineHopShapes(motisShape, futarShape, coversHead, rowShape) {
    const motis = BkkLib.validMapShape(motisShape);
    const futar = BkkLib.validMapShape(futarShape);
    const row = BkkLib.validMapShape(rowShape);
    if (motis.length >= 2 && futar.length >= 2) {
      const startGap = BkkLib.haversineMeters(motis[0], futar[0]);
      const sameCorridor = startGap <= 2500;
      if (!sameCorridor) {
        if (coversHead === false) {
          const stitched = BkkLib.stitchShapes(motis, futar);
          return stitched.length >= 2 ? stitched : motis;
        }
        return motis;
      }
      if (coversHead === false) {
        return BkkLib.pickFullerShape(BkkLib.stitchShapes(motis, futar), futar);
      }
      return BkkLib.pickFullerShape(motis, futar);
    }
    if (motis.length >= 2) return motis;
    if (futar.length >= 2) return futar;
    return row;
  },
  finiteLatLon(lat, lon) {
    if (lat == null || lon == null || lat === "" || lon === "") return null;
    const a = Number(lat);
    const b = Number(lon);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
    if (Math.abs(a) > 90 || Math.abs(b) > 180) return null;
    if (Math.abs(a) < 1 && Math.abs(b) < 1) return null;
    return [a, b];
  },
  /* Station coordinates as the MÁV GTFS stops have them (via Transitous),
     so a pin lands on the tracks: Székesfehérvár used to point at the city
     centre, 840 m from the station. */
  RAIL_HUB_COORDS: {
    "BKK_005501024": [47.46432, 19.02063],
    "BKK_005503269": [47.18361, 18.42472],
    "BKK_005510017": [47.50042, 19.08462],
    "BKK_CSF01131": [47.50042, 19.08462],
    "BKK_CS007896": [47.46432, 19.02063],
    "BKK_056216": [47.46432, 19.02063]
  },
  RAIL_HUB_ALIASES: {
    "kelenfold": [47.46432, 19.02063],
    "budapest kelenfold": [47.46432, 19.02063],
    "szekesfehervar": [47.18361, 18.42472],
    "keleti": [47.50042, 19.08462],
    "keleti palyaudvar": [47.50042, 19.08462],
    "deli": [47.49889, 19.025],
    "budapest deli": [47.49889, 19.025]
  },
  knownStopCoords(stop) {
    const id = String(stop && (stop.id || stop.stopId) || "");
    const byId = BkkLib.RAIL_HUB_COORDS[id];
    if (byId) return BkkLib.mapGpsLatLon(byId[0], byId[1]);
    const texts = [
      stop && stop.key,
      stop && stop.name,
      stop && stop.destKey
    ];
    for (let i = 0; i < texts.length; i++) {
      const folded = BkkLib.fold(texts[i] || "");
      if (!folded) continue;
      const hit = BkkLib.RAIL_HUB_ALIASES[folded];
      if (hit) return BkkLib.mapGpsLatLon(hit[0], hit[1]);
    }
    return null;
  },
  /* Live GPS for this card: drop null-island / empty 0,0 and anything
     outside the region Hungarian services actually run in. 0.47,0.19
     (a 1e2 mis-scale of Budapest) sits in the Gulf of Guinea. */
  mapGpsLatLon(lat, lon) {
    const pt = BkkLib.finiteLatLon(lat, lon);
    if (!pt) return null;
    const a = pt[0];
    const b = pt[1];
    if (Math.abs(a) < 1 && Math.abs(b) < 1) return null;
    if (a < 42 || a > 56 || b < 8 || b > 30) return null;
    return pt;
  },
  validMapShape(shape) {
    if (!Array.isArray(shape)) return [];
    return shape.map((pt) => {
      if (!Array.isArray(pt) || pt.length < 2) return null;
      return BkkLib.finiteLatLon(pt[0], pt[1]);
    }).filter(Boolean);
  },
  clipRideShape(shape, board, dest, row) {
    const pts = BkkLib.validMapShape(shape);
    if (pts.length < 2) return pts;
    if (BkkLib.keepsFullLineShape(row)) return pts;
    return BkkLib.clipShapeToBoardDest(pts, board, dest);
  },
  motisCoversHead(hit, row) {
    if (!hit) return false;
    const raw = String(row && (row.head || row.headsign || row.tripTo) || "").replace(PLATFORM_STRIP, "").trim();
    if (!raw) return true;
    return BkkLib.destOnMotisRow(hit, { name: raw, key: raw });
  },
  rideMapColor(leg) {
    const hex = String(leg && leg.color || "").replace("#", "");
    if (/^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$/.test(hex)) return "#" + hex;
    return "#0F6FC6";
  },
  MAP_ROUTE_PALETTE: [
    "#e53935",
    "#8e24aa",
    "#3949ab",
    "#00897b",
    "#f9a825",
    "#6d4c41",
    "#d81b60",
    "#00acc1",
    "#43a047",
    "#fb8c00",
    "#5c6bc0",
    "#00838f"
  ],
  /* Dark or white text, whichever reads on this line colour. */
  mapTextOn(color) {
    const hex = BkkLib.normMapHex(color);
    if (!hex) return "#ffffff";
    const lin = (i) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    const lum = 0.2126 * lin(1) + 0.7152 * lin(3) + 0.0722 * lin(5);
    return lum > 0.4 ? "#1a1a1a" : "#ffffff";
  },
  normMapHex(color) {
    const hex = String(color || "").replace("#", "").trim();
    if (/^[0-9a-fA-F]{3}$/.test(hex)) {
      return ("#" + hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2]).toLowerCase();
    }
    if (/^[0-9a-fA-F]{6}$/.test(hex)) return ("#" + hex).toLowerCase();
    return "";
  },
  mapRouteLabel(row) {
    return String(row && (row.label || row.route || "") || "").trim() || "?";
  },
  mapRouteColor(row, usedByLabel) {
    const label = BkkLib.mapRouteLabel(row);
    usedByLabel = usedByLabel || {};
    if (usedByLabel[label]) return usedByLabel[label];
    const used = {};
    Object.keys(usedByLabel).forEach((k) => {
      used[String(usedByLabel[k]).toLowerCase()] = true;
    });
    let color = BkkLib.normMapHex(row && row.color);
    if (color && used[color]) color = "";
    if (!color) {
      const palette = BkkLib.MAP_ROUTE_PALETTE;
      color = palette.find((c) => !used[c]) || palette[Object.keys(usedByLabel).length % palette.length];
    }
    usedByLabel[label] = color;
    return color;
  },
  legStartPoint(leg) {
    const sh = BkkLib.validMapShape(leg && leg.shape);
    if (sh.length) return sh[0];
    return BkkLib.finiteLatLon(leg && leg.fromLat, leg && leg.fromLon);
  },
  legEndPoint(leg) {
    const sh = BkkLib.validMapShape(leg && leg.shape);
    if (sh.length) return sh[sh.length - 1];
    return BkkLib.finiteLatLon(leg && leg.toLat, leg && leg.toLon);
  },
  stitchJourneyLegCoords(journey) {
    const legs = journey && journey.legs || [];
    for (let i = 0; i < legs.length; i++) {
      const leg = legs[i];
      if (BkkLib.legStartPoint(leg) || i < 1) continue;
      const prev = BkkLib.legEndPoint(legs[i - 1]);
      if (!prev) continue;
      if (leg.fromLat == null) {
        leg.fromLat = prev[0];
        leg.fromLon = prev[1];
      }
    }
    for (let i = legs.length - 1; i >= 0; i--) {
      const leg = legs[i];
      if (BkkLib.legEndPoint(leg) || i >= legs.length - 1) continue;
      const next = BkkLib.legStartPoint(legs[i + 1]);
      if (!next) continue;
      if (leg.toLat == null) {
        leg.toLat = next[0];
        leg.toLon = next[1];
      }
    }
    return journey;
  },
  /* The foot of the perpendicular from a point to every segment of a
     shape (repeated vertices skipped): `at` is metres along the shape, `d`
     the distance to it. */
  _shapeFeet(xy, cum, p) {
    const q = xy(p);
    const feet = [];
    for (let i = 1; i < xy.pts.length; i++) {
      const a = xy.pts[i - 1];
      const b = xy.pts[i];
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      const len2 = dx * dx + dy * dy;
      if (!len2) continue;
      const t2 = Math.max(0, Math.min(1, ((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / len2));
      const d = Math.hypot(a[0] + t2 * dx - q[0], a[1] + t2 * dy - q[1]);
      feet.push({ seg: i - 1, t: t2, d, at: cum[i - 1] + t2 * (cum[i] - cum[i - 1]) });
    }
    return feet;
  },
  /* Where a shape passes a point: each closest approach (a local minimum of
     the distance along the shape) no more than SHAPE_PASS_M farther than
     the closest one. Inside a segment its foot is one; at a vertex, only
     when the segment before ran towards it (that foot ends at t = 1, and
     the later foot, at t = 0, stands for the vertex). */
  _shapePasses(feet) {
    const limit = feet.reduce((m, f) => Math.min(m, f.d), Infinity) + SHAPE_PASS_M;
    return feet.filter((f, i) => f.d <= limit && (f.t > 0 && f.t < 1 || f.t === 0 && (i === 0 || feet[i - 1].t === 1) || f.t === 1 && i === feet.length - 1));
  },
  /* The part of a shape from `from` to `to`, in that direction. Both stops
     are projected onto the shape's segments, and every pass of the shape by
     each stop counts: the ride is the shortest stretch from a pass of
     `from` forward to a pass of `to`, a close pass winning over a far one.
     An out-and-back line passes both stops twice (Miskolc ZOO, Pécs 1), and
     only the pair in the direction of travel is short. Two stops on one
     segment give that short piece, not the whole line. A closed loop
     (Szeged 78 starts past Mars tér) may run on over its end; a line whose
     two ends only happen to meet is no loop. */
  sliceShapeBetween(shape, from, to) {
    const pts = BkkLib.validMapShape(shape);
    if (pts.length < 2) return pts;
    const a = BkkLib.finiteLatLon(from && from[0], from && from[1]);
    const b = BkkLib.finiteLatLon(to && to[0], to && to[1]);
    if (!a || !b) return pts;
    const k = Math.cos(a[0] * Math.PI / 180) * 111320;
    const xy = (p) => [p[1] * k, p[0] * 110540];
    xy.pts = pts.map(xy);
    const cum = [0];
    for (let i = 1; i < pts.length; i++) {
      cum.push(cum[i - 1] + Math.hypot(xy.pts[i][0] - xy.pts[i - 1][0], xy.pts[i][1] - xy.pts[i - 1][1]));
    }
    const total = cum[cum.length - 1];
    const gap = BkkLib.haversineMeters(pts[0], pts[pts.length - 1]);
    const loop = gap <= SHAPE_LOOP_CLOSE_M && total >= SHAPE_LOOP_RATIO * Math.max(gap, 20);
    const boards = BkkLib._shapePasses(BkkLib._shapeFeet(xy, cum, a));
    const alights = BkkLib._shapePasses(BkkLib._shapeFeet(xy, cum, b));
    if (!boards.length || !alights.length) return pts;
    let pick = null;
    const consider = (pa, pb) => {
      let span = pb.at - pa.at;
      if (span < 0 && loop) span += total;
      if (span < 0) return;
      const cost = span + SHAPE_PASS_WEIGHT * (pa.d + pb.d);
      if (!pick || cost < pick.cost) pick = { pa, pb, cost, wrap: pb.at < pa.at };
    };
    boards.forEach((pa) => alights.forEach((pb) => consider(pa, pb)));
    const foot = (f) => {
      const p = pts[f.seg];
      const q = pts[f.seg + 1];
      if (f.t === 0) return p;
      if (f.t === 1) return q;
      return [p[0] + (q[0] - p[0]) * f.t, p[1] + (q[1] - p[1]) * f.t];
    };
    const between = (pa, pb, wrap) => {
      const out = [foot(pa)];
      const push = (p) => {
        const last = out[out.length - 1];
        if (last[0] !== p[0] || last[1] !== p[1]) out.push(p);
      };
      if (wrap) {
        for (let i = pa.seg + 1; i < pts.length; i++) push(pts[i]);
        for (let i = 1; i <= pb.seg; i++) push(pts[i]);
      } else {
        for (let i = pa.seg + 1; i <= pb.seg; i++) push(pts[i]);
      }
      push(foot(pb));
      return out;
    };
    let sliced;
    if (pick) {
      sliced = between(pick.pa, pick.pb, pick.wrap);
    } else {
      const nearest = (list) => list.reduce((m, f) => f.d < m.d ? f : m, list[0]);
      sliced = between(nearest(alights), nearest(boards), false).reverse();
    }
    return sliced.length >= 2 ? sliced : [a, b];
  },
  clipShapeToBoardDest(shape, board, dest) {
    const pts = BkkLib.validMapShape(shape);
    const a = Array.isArray(board) ? BkkLib.mapGpsLatLon(board[0], board[1]) : BkkLib.mapGpsLatLon(board && board.lat, board && board.lon);
    const b = Array.isArray(dest) ? BkkLib.mapGpsLatLon(dest[0], dest[1]) : BkkLib.mapGpsLatLon(dest && dest.lat, dest && dest.lon);
    if (pts.length < 2 || !a || !b) return pts;
    const sliced = BkkLib.sliceShapeBetween(pts, a, b);
    if (sliced.length < 2) return pts;
    const span = BkkLib.shapeSpanMeters(sliced);
    const orig = BkkLib.shapeSpanMeters(pts);
    if (span < 200 && orig > 2e3) return pts;
    return sliced;
  },
  /* The card, its inline map and the full map all ask for the same
     journey's shapes: one fill per journey, its trips fetched once. */
  _journeyFills: /* @__PURE__ */ new WeakMap(),
  fillJourneyShapes(journey) {
    if (!journey || !Array.isArray(journey.legs)) return Promise.resolve(journey);
    let run = BkkLib._journeyFills.get(journey);
    if (!run) {
      run = BkkLib._fillJourneyShapes(journey);
      BkkLib._journeyFills.set(journey, run);
    }
    return run;
  },
  async _fillJourneyShapes(journey) {
    BkkLib.stitchJourneyLegCoords(journey);
    await Promise.all(journey.legs.map(async (leg) => {
      if (!leg || leg.walk) return;
      if (BkkLib.validMapShape(leg.shape).length >= 2) return;
      if (leg.gtfs) {
        const g = leg.gtfs;
        const pts = Array.isArray(g.stops) ? g.stops : [];
        const a = pts[g.from];
        const b = pts[g.to];
        let shape = [];
        try {
          shape = await BkkLib.gtfsShape(g.shape);
        } catch (_e) {
          shape = [];
        }
        if (shape.length >= 2 && a && b) shape = BkkLib.sliceShapeBetween(shape, a, b);
        if (shape.length < 2) shape = BkkLib.validMapShape(pts.slice(g.from, g.to + 1).filter(Boolean));
        if (shape.length >= 2) leg.shape = shape;
        return;
      }
      const tid = String(leg.tripId || "");
      if (!tid) return;
      try {
        let pts = await BkkLib.motisTripShape(tid);
        const a = BkkLib.finiteLatLon(leg.fromLat, leg.fromLon);
        const b = BkkLib.finiteLatLon(leg.toLat, leg.toLon);
        if (pts.length >= 2 && a && b) pts = BkkLib.sliceShapeBetween(pts, a, b);
        if (pts.length >= 2) leg.shape = pts;
      } catch (_e) {
      }
    }));
    BkkLib.stitchJourneyLegCoords(journey);
    return journey;
  },
  journeyMapSegments(journey, origin, dest) {
    BkkLib.stitchJourneyLegCoords(journey);
    const segs = [];
    const add = (shape, walk, color, from, to, leg) => {
      if (!Array.isArray(shape) || shape.length < 2) return;
      segs.push({
        walk: !!walk,
        color: walk ? "" : color || "#0F6FC6",
        label: walk ? "" : String(leg && leg.label || ""),
        text: walk ? "" : String(leg && leg.text || ""),
        shape,
        from: from || "",
        to: to || ""
      });
    };
    (journey && journey.legs || []).forEach((leg) => {
      let shape = BkkLib.validMapShape(leg.shape);
      if (shape.length < 2) {
        const a = BkkLib.finiteLatLon(leg.fromLat, leg.fromLon);
        const b = BkkLib.finiteLatLon(leg.toLat, leg.toLon);
        if (a && b) shape = [a, b];
      }
      add(shape, leg.walk, BkkLib.rideMapColor(leg), leg.from, leg.to, leg);
    });
    if (segs.length) return segs;
    const start = BkkLib.finiteLatLon(
      origin && (origin.stopLat != null ? origin.stopLat : origin.lat),
      origin && (origin.stopLon != null ? origin.stopLon : origin.lon)
    );
    const end = BkkLib.finiteLatLon(
      dest && (dest.destLat != null ? dest.destLat : dest.lat),
      dest && (dest.destLon != null ? dest.destLon : dest.lon)
    );
    if (start && end) add([start, end], false, "#0F6FC6", "", "");
    return segs;
  },
  /* Stops to mark on a journey map: where each ride boards and alights. A
     transfer at the same stop is one pin. A walk to or from the journey's
     ends gets a start or an end pin, unless it only crosses the same stop
     (Fő tér to the Fő tér pole, Astoria M to Astoria) or is so short that
     the pin would sit on the stop's own. */
  journeyStopPins(segs) {
    const list = Array.isArray(segs) ? segs : [];
    const pins = [];
    const rides = list.filter((s) => !s.walk && Array.isArray(s.shape) && s.shape.length >= 2);
    if (!rides.length) return pins;
    const first = list[0];
    const last = list[list.length - 1];
    const ownPin = (pt, name, ridePt, rideName) => BkkLib.haversineMeters(pt, ridePt) > 150 && !BkkLib.nameEq(name, rideName);
    if (first.walk && first.shape.length && ownPin(first.shape[0], first.from, rides[0].shape[0], rides[0].from)) {
      pins.push({ kind: "start", pt: first.shape[0], name: first.from || "" });
    }
    rides.forEach((seg) => {
      const board = seg.shape[0];
      const line = { color: seg.color, label: seg.label || "", text: seg.text || "" };
      const prev = pins[pins.length - 1];
      if (prev && prev.kind === "alight" && BkkLib.haversineMeters(prev.pt, board) <= 60) {
        Object.assign(prev, line, { kind: "transfer", name: prev.name || seg.from || "" });
      } else {
        pins.push(Object.assign({ kind: "board", pt: board, name: seg.from || "" }, line));
      }
      pins.push({ kind: "alight", pt: seg.shape[seg.shape.length - 1], name: seg.to || "", color: seg.color });
    });
    const lastRide = rides[rides.length - 1];
    const endPt = last.walk && last.shape.length ? last.shape[last.shape.length - 1] : null;
    if (endPt && ownPin(endPt, last.to, lastRide.shape[lastRide.shape.length - 1], lastRide.to)) {
      pins.push({ kind: "end", pt: endPt, name: last.to || "" });
    }
    return pins;
  },
  haversineMeters(a, b) {
    const toRad = (d) => d * Math.PI / 180;
    const R = 6371e3;
    const dLat = toRad(b[0] - a[0]);
    const dLon = toRad(b[1] - a[1]);
    const lat1 = toRad(a[0]);
    const lat2 = toRad(b[0]);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }
});

// src/tags.js
var BKK_HOP_TAG = "hungarian-transport-card";
var BKK_HOP_EDITOR = "hungarian-transport-card-editor";
var BKK_HOP_TAG_ALIAS = "bkk-stop-card-r3";
var BKK_HOP_EDITOR_ALIAS = "bkk-stop-card-r3-editor";
var BKK_PLANNER_EDITOR = "hungarian-transit-stop-card-plan-editor";
var BKK_PLANNER_EDITOR_ALIAS = "bkk-stop-card-plan-editor";
var OWN_CARD_TYPES = [BKK_HOP_TAG, BKK_HOP_TAG_ALIAS, BKK_PLANNER_TAG, BKK_PLANNER_TAG_ALIAS];
var INDEX_SCHEMA = 29;

// src/lib/dashboard-key.js
Object.assign(BkkLib, {
  /* DEPRECATED card-level key (review J-3, 2026-10-19). A key in the card YAML
     is sent to BKK as a query parameter (futar.js), and any logged-in Home
     Assistant user, admin or not, can read it with the lovelace/config call
     used below. It keeps working; the recommended place is the integration
     (the hungarian_transport.futar service), whose key never leaves Home
     Assistant. The card says so once per page, without the key itself. */
  _warnedCardKey: false,
  warnCardApiKey(cfg) {
    if (BkkLib._warnedCardKey || !cfg || typeof cfg.apiKey !== "string" || !cfg.apiKey.trim()) return false;
    BkkLib._warnedCardKey = true;
    try {
      console.warn("Hungarian transport: the card-level apiKey is deprecated. Every logged-in Home Assistant user can read it from the dashboard config, and it is sent to BKK in the request URL. Enter the key in the Hungarian transport integration instead and remove apiKey from the card.");
    } catch (_e) {
    }
    return true;
  },
  /* Only a key that sits on one of our own cards may be reused. A dashboard can
     hold apiKey values belonging to entirely unrelated cards, and those must
     never end up in a request to BKK. */
  findApiKey(node, acc) {
    if (!acc) acc = [];
    if (!node || typeof node !== "object") return acc;
    if (Array.isArray(node)) {
      node.forEach((x) => BkkLib.findApiKey(x, acc));
      return acc;
    }
    const type = String(node.type || "").replace(/^custom:/, "");
    if (typeof node.apiKey === "string" && node.apiKey && OWN_CARD_TYPES.indexOf(type) >= 0) {
      acc.push(node.apiKey);
    }
    Object.keys(node).forEach((k) => {
      if (k === "apiKey") return;
      BkkLib.findApiKey(node[k], acc);
    });
    return acc;
  },
  /* A dashboard usually holds several of these cards. Reuse the key already
     configured on a sibling card instead of asking for it again. Only the
     default dashboard and the one being viewed are inspected.
     Editors and planners ask on every hass update or keystroke while no key
     is found, and each ask is one or two full lovelace/config reads: a found
     key is kept for the page, a miss for a minute, concurrent asks share one. */
  _dashKeys: /* @__PURE__ */ new WeakMap(),
  apiKeyFromDashboard(hass) {
    if (!hass || !hass.connection) return Promise.resolve("");
    let byPanel = BkkLib._dashKeys.get(hass.connection);
    if (!byPanel) {
      byPanel = /* @__PURE__ */ new Map();
      BkkLib._dashKeys.set(hass.connection, byPanel);
    }
    const memo = String(hass.panelUrl || "");
    const hit = byPanel.get(memo);
    if (hit && (hit.p || hit.key || Date.now() - hit.ts < 6e4)) return hit.p || Promise.resolve(hit.key);
    const p = BkkLib._readDashboardKey(hass).then((key) => {
      byPanel.set(memo, { key, ts: Date.now() });
      return key;
    }, () => {
      byPanel.delete(memo);
      return "";
    });
    byPanel.set(memo, { p, key: "", ts: Date.now() });
    return p;
  },
  async _readDashboardKey(hass) {
    const paths = [null];
    if (hass.panelUrl && paths.indexOf(hass.panelUrl) < 0) paths.push(hass.panelUrl);
    for (let i = 0; i < paths.length; i++) {
      try {
        const msg = { type: "lovelace/config" };
        if (paths[i]) msg.url_path = paths[i];
        const cfg = await hass.connection.sendMessagePromise(msg);
        const keys = BkkLib.findApiKey(cfg);
        if (keys.length) return keys[0];
      } catch (_e) {
      }
    }
    return "";
  }
});

// src/lib/gtfs.js
var DEST_NEAR_M = 1500;
Object.assign(BkkLib, {
  /* Cached per resolved URL, so two cards pointing at different index files do
     not serve each other's data. The URL carries a daily cache-buster, which
     also expires the entry on a dashboard left open overnight; the day before's
     copy of the same file is dropped then, not kept for days (each decoded
     index is tens of MB on a wall tablet). */
  _idx: /* @__PURE__ */ new Map(),
  _indexBase(url) {
    return String(url).replace(/([?&])v=\d{8}(&|$)/, (_m, lead, tail) => tail ? lead : "").replace(/[?&]$/, "");
  },
  _cacheIndex(cache, url, p, max) {
    const base = BkkLib._indexBase(url);
    Array.from(cache.keys()).forEach((k) => {
      if (k !== url && BkkLib._indexBase(k) === base) cache.delete(k);
    });
    cache.set(url, p);
    while (cache.size > max) cache.delete(cache.keys().next().value);
  },
  /* A file that is not a usable index (an HTML page, a newer schema, bad
     gzip) stays that way: its failure is kept this long, as the MÁV index's
     is, instead of every lookup downloading and parsing it again. */
  _idxFailedAt: /* @__PURE__ */ new Map(),
  _idxFailRetryMs: 10 * 60 * 1e3,
  _indexCached(url, httpKey, gunzipKey) {
    const cache = BkkLib._idx;
    const hit = cache.get(url);
    if (hit) {
      const failedAt = BkkLib._idxFailedAt.get(url);
      if (!failedAt || Date.now() - failedAt < BkkLib._idxFailRetryMs) return hit;
      cache.delete(url);
    }
    BkkLib._idxFailedAt.delete(url);
    const lasting = /* @__PURE__ */ new Set(["errIndexSchema", "errIndexParse", gunzipKey]);
    const p = BkkLib._loadGtfsIndex(url, httpKey, gunzipKey).catch((err) => {
      if (err && lasting.has(err.i18nKey) && cache.get(url) === p) {
        BkkLib._idxFailedAt.set(url, Date.now());
      } else if (cache.get(url) === p) {
        cache.delete(url);
      }
      throw err;
    });
    BkkLib._cacheIndex(cache, url, p, 8);
    return p;
  },
  /* One card's view of the library: the same functions, with that card's own
     index URLs (volanIndex / cityIndex / mavIndex from its config). The lookups
     reach the indexes through `this`, so a card's override follows its own
     calls across every await and never leaks to another card (review J-2).
     A card without overrides gets the library itself. */
  withIndexes(cfg) {
    const c = cfg || {};
    const ix = { volan: String(c.volanIndex || ""), city: String(c.cityIndex || ""), mav: String(c.mavIndex || "") };
    if (!ix.volan && !ix.city && !ix.mav) return BkkLib;
    return Object.create(BkkLib, { _ix: { value: ix } });
  },
  volanIndex() {
    return BkkLib._indexCached(volanIndexUrl(this && this._ix && this._ix.volan), "errVolanHttp", "errVolanGunzip");
  },
  cityIndex() {
    return BkkLib._indexCached(cityIndexUrl(this && this._ix && this._ix.city), "errCityHttp", "errCityGunzip");
  },
  _mavP: /* @__PURE__ */ new Map(),
  /* When the index came back empty or failed: url -> the time of that answer.
     Opening a train map must not wait on a stalled server again for a while. */
  _mavEmptyAt: /* @__PURE__ */ new Map(),
  _mavEmptyRetryMs: 10 * 60 * 1e3,
  mavIndex() {
    const url = mavIndexUrl(this && this._ix && this._ix.mav);
    const cache = BkkLib._mavP;
    const hit = cache.get(url);
    if (hit) {
      const emptyAt = BkkLib._mavEmptyAt.get(url);
      if (!emptyAt || Date.now() - emptyAt < BkkLib._mavEmptyRetryMs) return hit;
      cache.delete(url);
      BkkLib._mavEmptyAt.delete(url);
    }
    const empty = () => ({ n: {}, l: {}, p: [] });
    const p = BkkLib._loadMavIndex(url).then((idx) => {
      const ok = idx && typeof idx === "object" && Array.isArray(idx.p) && idx.p.length > 0;
      if (ok) BkkLib._mavEmptyAt.delete(url);
      else BkkLib._mavEmptyAt.set(url, Date.now());
      return idx && typeof idx === "object" ? idx : empty();
    }).catch(() => {
      BkkLib._mavEmptyAt.set(url, Date.now());
      return empty();
    });
    BkkLib._cacheIndex(cache, url, p, 4);
    return p;
  },
  /* Headers and body under one timeout: a body that stalls after its
     headers must not leave an index promise pending until the page reloads.
     no-cache revalidates: Home Assistant answers 304 for an unchanged index,
     so a dashboard reload does not download megabytes again, while a rebuilt
     index (new ETag) is fetched at once. Resolves { status, bytes }, with
     bytes null on an HTTP error; rejects on a network error or timeout. */
  async _fetchIndexBytes(url) {
    const ctrl = typeof AbortController === "function" ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), BkkLib._indexTimeoutMs) : null;
    try {
      const res = await fetch(url, { cache: "no-cache", signal: ctrl ? ctrl.signal : void 0 });
      if (!res.ok) return { status: res.status, bytes: null };
      return { status: res.status, bytes: new Uint8Array(await res.arrayBuffer()) };
    } finally {
      if (timer) clearTimeout(timer);
    }
  },
  _indexTimeoutMs: 45e3,
  /* Index text, gunzipped when gzip; null when this browser cannot gunzip. */
  async _indexText(u8) {
    if (u8.length >= 2 && u8[0] === 31 && u8[1] === 139) {
      if (typeof DecompressionStream !== "function") return null;
      const stream = new Blob([u8]).stream().pipeThrough(new DecompressionStream("gzip"));
      return new Response(stream).text();
    }
    return new TextDecoder().decode(u8);
  },
  async _loadMavIndex(url) {
    let got;
    try {
      got = await BkkLib._fetchIndexBytes(url);
    } catch (_err) {
      return { n: {}, l: {}, p: [] };
    }
    if (!got.bytes) return { n: {}, l: {}, p: [] };
    const text = await BkkLib._indexText(got.bytes);
    if (text == null) return { n: {}, l: {}, p: [] };
    let idx;
    try {
      idx = JSON.parse(text);
    } catch (_e) {
      return { n: {}, l: {}, p: [] };
    }
    if (!idx || typeof idx !== "object" || !idx.n || !Array.isArray(idx.p)) {
      return { n: {}, l: {}, p: [] };
    }
    return {
      v: Number(idx.v) || 1,
      feed: String(idx.feed || ""),
      st: Array.isArray(idx.st) ? idx.st : [],
      n: idx.n && typeof idx.n === "object" ? idx.n : {},
      l: idx.l && typeof idx.l === "object" ? idx.l : {},
      p: idx.p
    };
  },
  mavShapeIndexKey(row, idx) {
    const num = BkkLib.rowTrainNumber(row);
    const label = String(row && (row.label || row.route || "") || "").trim().toUpperCase();
    if (!idx) return null;
    const line = (label.match(/^[SGZ]\d{2}/) || [])[0];
    if (BkkLib.suburbanLineLabel(label) && line && idx.l && idx.l[line] != null) {
      return { kind: "l", key: line, ix: idx.l[line] };
    }
    if (num && idx.n && idx.n[num] != null) {
      return { kind: "n", key: num, ix: idx.n[num] };
    }
    if (label && idx.l && idx.l[label] != null) {
      return { kind: "l", key: label, ix: idx.l[label] };
    }
    return null;
  },
  /* Schema 2 keeps every stop pattern of a train (936 sometimes runs on to
     Szombathely) with its stations: take the longest one that serves the
     card's two stations in order, else one serving the boarding station.
     Pins go on that pattern's own station coordinates. */
  pickMavVariant(variants, stations, boardName, alightName) {
    const same = (i, name) => !!name && BkkLib.nameEq(((stations || [])[i] || [])[0] || "", name);
    let firstBoard = null;
    for (const variant of variants || []) {
      const ids = variant && variant[1] || [];
      const b = ids.findIndex((i) => same(i, boardName));
      if (b < 0) continue;
      const a = ids.findIndex((i, k) => k > b && same(i, alightName));
      if (a > b) return { variant, board: b, alight: a };
      if (!firstBoard) firstBoard = { variant, board: b, alight: -1 };
    }
    if (firstBoard) return firstBoard;
    return variants && variants[0] ? { variant: variants[0], board: -1, alight: -1 } : null;
  },
  async mavRouteForRow(row, boardName, alightName) {
    try {
      const idx = await this.mavIndex();
      if (!idx || !Array.isArray(idx.p)) return null;
      const hit = BkkLib.mavShapeIndexKey(row, idx);
      if (!hit) return null;
      if (!Array.isArray(hit.ix)) {
        const shape2 = BkkLib.validMapShape(BkkLib.decodePolyline(idx.p[hit.ix] || ""));
        return shape2.length >= 2 ? { shape: shape2, board: null, alight: null } : null;
      }
      const pick = BkkLib.pickMavVariant(hit.ix, idx.st, boardName, alightName);
      if (!pick) return null;
      const shape = BkkLib.validMapShape(BkkLib.decodePolyline(idx.p[pick.variant[0]] || ""));
      if (shape.length < 2) return null;
      const at = (pos) => {
        const st = pos >= 0 ? (idx.st || [])[pick.variant[1][pos]] : null;
        return st ? BkkLib.mapGpsLatLon(st[1] / 1e5, st[2] / 1e5) : null;
      };
      return { shape, board: at(pick.board), alight: at(pick.alight) };
    } catch (_e) {
      return null;
    }
  },
  async _loadGtfsIndex(url, httpKey, gunzipKey) {
    let got;
    try {
      got = await BkkLib._fetchIndexBytes(url);
    } catch (_err) {
      throw codedError("errIndexFetch");
    }
    if (!got.bytes) throw codedError(httpKey, got.status);
    const text = await BkkLib._indexText(got.bytes);
    if (text == null) throw codedError(gunzipKey);
    let idx;
    try {
      idx = JSON.parse(text);
    } catch (_e) {
      throw codedError("errIndexParse");
    }
    if (!idx || typeof idx !== "object" || !Array.isArray(idx.s) || !Array.isArray(idx.t)) {
      throw codedError("errIndexParse");
    }
    if (idx.v && Number(idx.v) > INDEX_SCHEMA) throw codedError("errIndexSchema", idx.v);
    const g = Array.isArray(idx.g) && idx.g.length === 2 * idx.s.length ? idx.g : null;
    idx.stops = (idx.s || []).map((row, i) => ({
      i,
      id: row[0],
      name: row[1],
      num: String(row[2] || ""),
      op: row.length > 3 ? Number(row[3]) : 0,
      key: BkkLib.norm(row[1]),
      fold: BkkLib.fold(row[1]),
      lat: g && (g[2 * i] || g[2 * i + 1]) ? g[2 * i] / 1e5 : null,
      lon: g && (g[2 * i] || g[2 * i + 1]) ? g[2 * i + 1] / 1e5 : null
    }));
    idx.g = null;
    idx.s = null;
    idx.url = BkkLib._absoluteUrl(url);
    idx.ops = (idx.o || []).map((row, i) => ({ i, id: row[0], name: row[1] }));
    idx.byNum = /* @__PURE__ */ new Map();
    idx.byKey = /* @__PURE__ */ new Map();
    idx.byId = /* @__PURE__ */ new Map();
    idx.stops.forEach((st) => {
      const nk = st.op + ":" + st.num;
      const kk = st.op + ":" + st.key;
      if (st.num && !idx.byNum.has(nk)) idx.byNum.set(nk, st);
      if (st.key && !idx.byKey.has(kk)) idx.byKey.set(kk, st);
      if (st.id && !idx.byId.has(st.id)) idx.byId.set(st.id, st);
    });
    return idx;
  },
  _absoluteUrl(url) {
    const doc = typeof document !== "undefined" ? document : null;
    const loc = typeof location !== "undefined" ? location : null;
    const base = doc && doc.baseURI || loc && loc.href || void 0;
    try {
      return new URL(String(url), base).href;
    } catch (_e) {
      return String(url);
    }
  },
  gtfsStopById(idx, id) {
    if (!idx || !id) return null;
    const key = String(id);
    if (idx.byId) return idx.byId.get(key) || null;
    return (idx.stops || []).find((s) => s.id === key) || null;
  },
  /* The town of a city feed's operator ("Székesfehérvár — helyi" →
     Székesfehérvár); '' for the Volán index, whose names carry the town. */
  gtfsOpTown(idx, opIx) {
    const op = idx && opIx != null ? (idx.ops || [])[opIx] : null;
    return op ? String(op.name || "").split(" — ")[0].trim() : "";
  },
  /* A stop of an index as a picker hit: with its coordinates and its town,
     so planning starts from that stop and not from whatever a geocoder
     finds under the bare name (Tatabánya "Fő tér" in Budapest). */
  gtfsHit(idx, s, extra) {
    const hit = Object.assign({ id: s.id, name: s.name }, extra || {});
    if (BkkLib.finiteLatLon(s.lat, s.lon)) {
      hit.lat = s.lat;
      hit.lon = s.lon;
    }
    const area = idx && idx.ops && idx.ops.length ? BkkLib.gtfsOpTown(idx, s.op) : "";
    if (area) hit.area = area;
    return hit;
  },
  /* Picked stops that came without coordinates (a city search hit, an
     older history entry, a YAML origin) get them from the index they
     belong to. Anything else passes through. */
  async withGtfsCoords(stops) {
    const list = Array.isArray(stops) ? stops : [];
    const need = (s) => s && s.id && !BkkLib.finiteLatLon(s.lat, s.lon);
    const cityish = (s) => need(s) && BkkLib.stopIdFamily(s) === "city";
    const volanish = (s) => need(s) && /^(?:hkir_|volan_)/i.test(String(s.id));
    const load = async (want, get) => {
      if (!list.some(want)) return null;
      try {
        return await get();
      } catch (_e) {
        return null;
      }
    };
    const [city, volan] = await Promise.all([
      load(cityish, () => this.cityIndex()),
      load(volanish, () => this.volanIndex())
    ]);
    return list.map((s) => {
      const idx = cityish(s) ? city : volanish(s) ? volan : null;
      const hit = idx ? BkkLib.gtfsStopById(idx, s.id) : null;
      if (!hit || !BkkLib.finiteLatLon(hit.lat, hit.lon)) return s;
      const out = Object.assign({}, s, { lat: hit.lat, lon: hit.lon });
      const area = idx.ops && idx.ops.length ? BkkLib.gtfsOpTown(idx, hit.op) : "";
      if (area && !out.area) out.area = area;
      return out;
    });
  },
  opIx(idx, city) {
    if (!idx || !city) return -1;
    const hit = (idx.ops || []).find((o) => o.id === city);
    return hit ? hit.i : -1;
  },
  cityStops(idx, opIx) {
    if (opIx < 0) return idx.stops || [];
    return (idx.stops || []).filter((s) => s.op === opIx);
  },
  /* One GTFS service day. GTFS times count from noon minus 12 h, not from
     midnight: on the two DST days midnight is an hour off that base, and
     every departure of the day would shift by an hour. `base` is that moment
     in epoch seconds and `mins` is now in the day's own GTFS minutes: about
     now + 24h for yesterday (its 24:xx trips are tonight's), negative for
     tomorrow. */
  volanDay(offset, nowMs) {
    const off = offset || 0;
    const at = nowMs == null ? Date.now() : nowMs;
    const ymd = tzShiftYmd(tzParts(new Date(at)).ymd, off);
    const base = BkkLib.gtfsDayBase(ymd);
    return {
      ymd,
      dow: tzDow(ymd),
      mins: Math.floor((at / 1e3 - base) / 60),
      base
    };
  },
  gtfsDayBase(ymd) {
    return tzEpochSec(ymd, 720) - 12 * 3600;
  },
  volanServiceOk(idx, si, ymd, dow) {
    const c = (idx.c || [])[si];
    if (!c) return false;
    const days = String(c[0] || "0000000");
    const start = String(c[1] || "");
    const end = String(c[2] || "");
    let ok = ymd >= start && ymd <= end && days.charAt(dow) === "1";
    const add = (idx.a || [])[si] || [];
    const rem = (idx.r || [])[si] || [];
    if (add.some((x) => String(x) === ymd)) ok = true;
    if (rem.some((x) => String(x) === ymd)) ok = false;
    return ok;
  },
  cityOpId(idx, stop) {
    if (!idx || !stop) return "";
    const id = String(stop.id || "");
    const hit = BkkLib.gtfsStopById(idx, id);
    if (hit && idx.ops && idx.ops[hit.op]) return idx.ops[hit.op].id;
    const colon = id.indexOf(":");
    const prefix = colon > 0 ? id.slice(0, colon) : "";
    const op = (idx.ops || []).find((o) => o.id === prefix);
    return op ? op.id : "";
  },
  cityStopIndexes(idx, stop, opIx) {
    const origin = BkkLib.volanMatchStop(idx, stop, opIx);
    if (!origin) return [];
    const pool = BkkLib.cityStops(idx, opIx);
    const out = [];
    pool.forEach((s) => {
      if (s.fold === origin.fold && s.op === origin.op) out.push(s.i);
    });
    return out.length ? out : [origin.i];
  },
  volanMatchStop(idx, stop, opIx) {
    if (!idx || !stop) return null;
    const op = opIx == null ? 0 : opIx;
    const pool = opIx == null ? idx.stops : BkkLib.cityStops(idx, op);
    const num = BkkLib.stopNum(stop.id);
    if (num && idx.byNum.has(op + ":" + num)) {
      const hit = idx.byNum.get(op + ":" + num);
      if (opIx == null || hit.op === op) return hit;
    }
    const key = BkkLib.norm(stop.name || "");
    if (key && idx.byKey.has(op + ":" + key)) return idx.byKey.get(op + ":" + key);
    const f = BkkLib.fold(stop.name || "");
    if (!f || f.length < 4) return null;
    const exact = pool.find((s) => s.fold === f);
    if (exact) return exact;
    const hits = pool.filter((s) => s.fold.indexOf(f) >= 0);
    if (!hits.length) return null;
    hits.sort((a, b) => {
      const ah = BkkLib.isCoachStationFold(a.fold) ? 0 : 1;
      const bh = BkkLib.isCoachStationFold(b.fold) ? 0 : 1;
      if (ah !== bh) return ah - bh;
      return a.fold.length - b.fold.length;
    });
    return hits[0];
  },
  /* A coach station (or a "pályaudvar") by its folded name: fold() turns
     "autóbusz-állomás" into "autobusz allomas"; some feeds write it closed. */
  isCoachStationFold(folded) {
    return /autobusz ?allomas|palyaudvar/.test(String(folded || ""));
  },
  /* Folded town names of an index: the "Town, " part of its stop names
     (the Volán feed names every stop so) and its operators' towns. */
  _gtfsTowns: /* @__PURE__ */ new WeakMap(),
  gtfsTowns(idx) {
    if (!idx || typeof idx !== "object") return /* @__PURE__ */ new Set();
    let towns = BkkLib._gtfsTowns.get(idx);
    if (towns) return towns;
    towns = /* @__PURE__ */ new Set();
    (idx.stops || []).forEach((st) => {
      const split = BkkLib.splitCityPrefix(st && st.name);
      if (split) towns.add(BkkLib.fold(split.city));
    });
    (idx.ops || []).forEach((op) => {
      const town = BkkLib.fold(BkkLib.gtfsOpTown(idx, op.i));
      if (town) towns.add(town);
    });
    BkkLib._gtfsTowns.set(idx, towns);
    return towns;
  },
  /* Whether a stop of a trip is the destination. A town ("Tata", "Eger")
     is any stop of that town, as a whole word: "Tata, vasútállomás" but not
     "Tatabánya", nor "Egerszalók". Any other name has to be that place:
     "Kórház" is not "Kórház tér" nor "Megyei Kórház", and when both name a
     town it is the same one. With the destination's coordinates, a stop of
     that name in another town (11 towns have a "Kossuth tér") is too far.
     opts: { town, lat, lon }, and the destination's place key and town
     (`place`, `townOf`) when the caller has them worked out already. */
  volanDestHit(st, destKey, destFold, opts) {
    if (!st) return false;
    opts = opts || {};
    const fk = destFold || BkkLib.fold(destKey || "");
    if (opts.town) {
      if (destKey && (st.key === destKey || st.fold === fk)) return true;
      return !!fk && fk.length >= 4 && (" " + st.fold + " ").indexOf(" " + fk + " ") >= 0;
    }
    const want = BkkLib.mapGpsLatLon(opts.lat, opts.lon);
    const here = BkkLib.mapGpsLatLon(st.lat, st.lon);
    if (want && here && BkkLib.haversineMeters(want, here) > DEST_NEAR_M) return false;
    if (destKey && (st.key === destKey || st.fold === fk)) return true;
    const name = destKey || fk;
    const place = opts.place != null ? opts.place : BkkLib.placeNameKey({ name });
    const mine = BkkLib._stopPlace(st);
    if (!place || place !== mine.place) return false;
    const town = opts.townOf != null ? opts.townOf : BkkLib._townOf(name);
    return !(town && mine.town && town !== mine.town);
  },
  /* An index stop's place key and town, worked out once per stop. */
  _stopPlaces: /* @__PURE__ */ new WeakMap(),
  _stopPlace(st) {
    let hit = BkkLib._stopPlaces.get(st);
    if (!hit) {
      const name = st.name || st.key || "";
      hit = { place: BkkLib.placeNameKey({ name }), town: BkkLib._townOf(name) };
      BkkLib._stopPlaces.set(st, hit);
    }
    return hit;
  },
  _townOf(name) {
    const split = BkkLib.splitCityPrefix(name);
    return split ? BkkLib.fold(split.city) : "";
  },
  async volanSearchStops(q) {
    const idx = await this.volanIndex();
    return BkkLib.gtfsSearchStops(idx, q, null);
  },
  async volanRoutesAtStop(stop) {
    const idx = await this.volanIndex();
    const origin = BkkLib.volanMatchStop(idx, stop, null);
    if (!origin) return [];
    const seen = /* @__PURE__ */ new Set();
    const out = [];
    const trips = idx.t || [];
    for (let i = 0; i < trips.length; i++) {
      const route = trips[i][1];
      if (!route || seen.has(route)) continue;
      const ps = trips[i][2] || [];
      if (ps.indexOf(origin.i) < 0) continue;
      seen.add(route);
      out.push({
        id: "gtfs:" + route,
        label: String(route),
        color: "#F9AB13",
        text: "#000000",
        type: "COACH",
        source: "gtfs"
      });
    }
    out.sort((a, b) => a.label.localeCompare(b.label, "hu", { numeric: true }));
    return out;
  },
  async volanRemainingNames(stop, routeLabel) {
    const idx = await this.volanIndex();
    const origin = BkkLib.volanMatchStop(idx, stop, null);
    if (!origin || !routeLabel) return [];
    const want = String(routeLabel);
    const byKey = /* @__PURE__ */ new Map();
    const trips = idx.t || [];
    for (let i = 0; i < trips.length; i++) {
      if (String(trips[i][1]) !== want) continue;
      const ps = trips[i][2] || [];
      const oidx = ps.indexOf(origin.i);
      if (oidx < 0) continue;
      for (let j = oidx + 1; j < ps.length; j++) {
        const st = idx.stops[ps[j]];
        if (!st || !st.key || byKey.has(st.key)) continue;
        byKey.set(st.key, { key: st.key, name: st.name, id: st.id });
      }
    }
    return Array.from(byKey.values());
  },
  citySearchAll(idx, q) {
    const f = BkkLib.fold(q);
    if (!f || !idx) return [];
    const best = /* @__PURE__ */ new Map();
    (idx.stops || []).forEach((s) => {
      const fold = s.fold || BkkLib.fold(s.name);
      if (!fold || fold.indexOf(f) < 0) return;
      const op = (idx.ops || [])[s.op];
      if (!op) return;
      const prev = best.get(op.id);
      const start = fold.startsWith(f) ? 0 : 1;
      if (!prev || start < prev.start || start === prev.start && s.name.localeCompare(prev.stop.name, "hu") < 0) {
        best.set(op.id, { stop: s, start });
      }
    });
    return Array.from(best.values()).map((row) => row.stop);
  },
  async citySearchStops(q, city) {
    if (!city) return [];
    const idx = await this.cityIndex();
    return BkkLib.gtfsSearchStops(idx, q, BkkLib.opIx(idx, city));
  },
  gtfsSearchStops(idx, q, opIx) {
    const f = BkkLib.fold(q);
    if (!f) return [];
    const pool = opIx == null || opIx < 0 ? idx.stops : BkkLib.cityStops(idx, opIx);
    const hits = pool.filter((s) => s.fold.indexOf(f) >= 0);
    hits.sort((a, b) => {
      const ah = BkkLib.isCoachStationFold(a.fold) ? 0 : 1;
      const bh = BkkLib.isCoachStationFold(b.fold) ? 0 : 1;
      if (ah !== bh) return ah - bh;
      const as = a.fold.startsWith(f) ? 0 : 1;
      const bs = b.fold.startsWith(f) ? 0 : 1;
      if (as !== bs) return as - bs;
      return a.name.localeCompare(b.name, "hu");
    });
    return hits.slice(0, 20).map((s) => BkkLib.gtfsHit(idx, s, { label: s.name, type: "BUS" }));
  },
  async volanReachableDests(stop) {
    const idx = await this.volanIndex();
    return BkkLib.gtfsReachableDests(idx, stop, null);
  },
  async cityReachableDests(stop, city) {
    if (!city) return { dests: [], destRoutes: {} };
    const idx = await this.cityIndex();
    return BkkLib.gtfsReachableDests(idx, stop, BkkLib.opIx(idx, city));
  },
  gtfsReachableDests(idx, stop, opIx) {
    const origin = BkkLib.volanMatchStop(idx, stop, opIx == null ? null : opIx);
    if (!origin) return { dests: [], destRoutes: {} };
    const indexes = opIx == null ? [origin.i] : BkkLib.cityStopIndexes(idx, stop, opIx);
    const byKey = /* @__PURE__ */ new Map();
    const destRoutes = {};
    const trips = idx.t || [];
    for (let i = 0; i < trips.length; i++) {
      const t2 = trips[i];
      const route = t2[1];
      const ps = t2[2] || [];
      const oidx = ps.findIndex((p) => indexes.indexOf(p) >= 0);
      if (oidx < 0) continue;
      for (let j = oidx + 1; j < ps.length; j++) {
        const st = idx.stops[ps[j]];
        if (!st || !st.key) continue;
        if (opIx != null && st.op !== opIx) continue;
        if (!byKey.has(st.key)) byKey.set(st.key, BkkLib.gtfsHit(idx, st, { key: st.key }));
        if (!destRoutes[st.key]) destRoutes[st.key] = [];
        if (destRoutes[st.key].indexOf(route) < 0) destRoutes[st.key].push(route);
      }
    }
    const dests = Array.from(byKey.values());
    dests.sort((a, b) => a.name.localeCompare(b.name, "hu"));
    return { dests, destRoutes };
  },
  async volanDepartures(stopId, dest, originName, minutesAfter) {
    const idx = await this.volanIndex();
    return BkkLib.gtfsDepartures(idx, stopId, dest, originName, null, "#F9AB13", "#000000", "coach", "COACH", minutesAfter);
  },
  async cityDepartures(stopId, dest, originName, city, minutesAfter) {
    if (!city) return [];
    const idx = await this.cityIndex();
    return BkkLib.gtfsDepartures(idx, stopId, dest, originName, BkkLib.opIx(idx, city), "#0F6FC6", "#ffffff", "bus", "BUS", minutesAfter);
  },
  /* GTFS route_type (basic and extended codes) to the card's vehicle kind.
     Buses keep the caller's default: coach for Volán, bus for city feeds. */
  gtfsRouteKind(routeType, vehicle, rawType) {
    const rt = Number(routeType);
    if (!Number.isFinite(rt)) return { vehicle, rawType };
    if (rt === 0 || rt >= 900 && rt < 1e3) return { vehicle: "tram", rawType: "TRAM" };
    if (rt === 1 || rt >= 400 && rt < 500) return { vehicle: "subway", rawType: "SUBWAY" };
    if (rt === 2 || rt >= 100 && rt < 200 || rt === 300) return { vehicle: "rail", rawType: "RAIL" };
    if (rt === 4 || rt >= 1e3 && rt < 1100 || rt === 1200) return { vehicle: "ferry", rawType: "FERRY" };
    if (rt === 11 || rt === 800) return { vehicle: "trolleybus", rawType: "TROLLEYBUS" };
    return { vehicle, rawType };
  },
  /* Where a GTFS trip runs: its stops' coordinates and a reference into the
     sharded shapes folder (trip row element 5), carried on the departure row
     so the map needs neither the index nor Transitous. */
  gtfsTripGeo(idx, t2, from, to) {
    const ps = t2 && t2[2] || [];
    const stops = ps.map((p) => {
      const st = idx.stops[p];
      return st && st.lat != null ? [st.lat, st.lon] : null;
    });
    const ref = Number(t2 && t2[5]);
    const sh = idx.sh;
    return {
      shape: sh && sh.dir && sh.n > 0 && Number.isInteger(ref) && ref >= 0 ? { base: idx.url, dir: sh.dir, n: sh.n, id: ref } : null,
      stops,
      from,
      to: to >= 0 ? to : ps.length - 1
    };
  },
  _shapeShards: /* @__PURE__ */ new Map(),
  async _fetchJsonGz(url, timeoutMs) {
    const ctrl = typeof AbortController === "function" ? new AbortController() : null;
    const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs || 2e4) : null;
    try {
      const res = await fetch(url, { signal: ctrl ? ctrl.signal : void 0 });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = await res.arrayBuffer();
      const u8 = new Uint8Array(buf);
      if (u8.length >= 2 && u8[0] === 31 && u8[1] === 139) {
        if (typeof DecompressionStream !== "function") throw new Error("no gzip");
        const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
        return JSON.parse(await new Response(stream).text());
      }
      return JSON.parse(new TextDecoder().decode(u8));
    } finally {
      if (timer) clearTimeout(timer);
    }
  },
  /* One trip's shapes.txt polyline. Shard id % n holds it at id / n; the
     folder name carries a content hash, so the browser cache can keep it. */
  async gtfsShape(ref) {
    if (!ref || !ref.dir || !(ref.n > 0) || !(ref.id >= 0)) return [];
    const k = ref.id % ref.n;
    const url = new URL(`${ref.dir}/${String(k).padStart(2, "0")}.json.gz`, ref.base).href;
    let p = BkkLib._shapeShards.get(url);
    if (!p) {
      p = BkkLib._fetchJsonGz(url).catch((err) => {
        BkkLib._shapeShards.delete(url);
        throw err;
      });
      BkkLib._shapeShards.set(url, p);
      while (BkkLib._shapeShards.size > 16) BkkLib._shapeShards.delete(BkkLib._shapeShards.keys().next().value);
    }
    const shard = await p;
    const enc = shard && Array.isArray(shard.p) ? shard.p[Math.floor(ref.id / ref.n)] : "";
    return enc ? BkkLib.validMapShape(BkkLib.decodePolyline(enc)) : [];
  },
  /* GTFS geometry for a departure row: the shapes.txt route (the whole line,
     or the hop for kinds the card clips), the boarding and alighting stops,
     and the stop-to-stop line as a last resort. Null for non-GTFS rows. */
  async gtfsRowShape(row) {
    const geo = row && row.gtfs;
    if (!geo) return null;
    const pts = Array.isArray(geo.stops) ? geo.stops : [];
    const board = pts[geo.from] || null;
    const dest = pts[geo.to] || null;
    let shape = [];
    try {
      shape = await BkkLib.gtfsShape(geo.shape);
    } catch (_e) {
      shape = [];
    }
    if (shape.length >= 2 && !BkkLib.keepsFullLineShape(row) && board && dest) {
      const hop = BkkLib.clipShapeToBoardDest(shape, board, dest);
      if (hop.length >= 2) shape = hop;
    }
    return { shape, board, dest, stopLine: BkkLib.validMapShape(pts.filter(Boolean)) };
  },
  /* City name of a local operator ("Székesfehérvár — helyi" → Székesfehérvár),
     so Transitous stop lookups stay inside that city. */
  async stopArea(cfg) {
    if (BkkLib.mode(cfg || {}) !== "helyi" || !cfg.city) return "";
    try {
      const idx = await this.cityIndex();
      const op = (idx.ops || []).find((o) => o.id === cfg.city);
      return op ? String(op.name || "").split(" — ")[0].trim() : "";
    } catch (_e) {
      return "";
    }
  },
  gtfsDepartures(idx, stopId, dest, originName, opIx, color, text, vehicle, rawType, minutesAfter) {
    const origin = BkkLib.volanMatchStop(idx, { id: stopId, name: originName || "" }, opIx == null ? null : opIx);
    if (!origin) return [];
    const indexes = opIx == null ? [origin.i] : BkkLib.cityStopIndexes(idx, { id: stopId, name: originName || "" }, opIx);
    const destKey = dest && dest.key;
    const destFold = BkkLib.fold(dest && dest.name || destKey || "");
    const destName = destKey || destFold;
    const destOpts = {
      town: !!destFold && !BkkLib.splitCityPrefix(dest && dest.name || destKey || "") && BkkLib.gtfsTowns(idx).has(destFold),
      lat: dest && dest.lat,
      lon: dest && dest.lon,
      place: BkkLib.placeNameKey({ name: destName }),
      townOf: BkkLib._townOf(destName)
    };
    const horizon = clampMinutesAfter(minutesAfter);
    const rowCap = maxDepartureRows(horizon);
    const rows = [];
    const trips = idx.t || [];
    const dayOffsets = [-1, 0, 1];
    for (let n = 0; n < dayOffsets.length; n++) {
      const day = BkkLib.volanDay(dayOffsets[n]);
      const earliest = day.mins - 1;
      const latest = day.mins + horizon;
      if (latest < 0) continue;
      for (let i = 0; i < trips.length; i++) {
        const t2 = trips[i];
        if (!BkkLib.volanServiceOk(idx, t2[0], day.ymd, day.dow)) continue;
        const route = t2[1];
        const ps = t2[2] || [];
        const ms = t2[3] || [];
        const oidx = ps.findIndex((p) => indexes.indexOf(p) >= 0);
        if (oidx < 0) continue;
        const depMin = ms[oidx];
        if (depMin == null || depMin < earliest) continue;
        if (depMin > latest) continue;
        let didx = -1;
        if (destKey || destFold) {
          for (let j = oidx + 1; j < ps.length; j++) {
            if (BkkLib.volanDestHit(idx.stops[ps[j]], destKey, destFold, destOpts)) {
              didx = j;
              break;
            }
          }
          if (didx < 0) continue;
        }
        const destStop = didx >= 0 ? idx.stops[ps[didx]] : idx.stops[ps[ps.length - 1]];
        const arrMin = didx >= 0 ? ms[didx] : null;
        const travel = arrMin != null && arrMin >= depMin && arrMin - depMin <= 1440 ? arrMin - depMin : null;
        const depTs = day.base + depMin * 60;
        if (!departureStillDue(depTs)) continue;
        const kind = BkkLib.gtfsRouteKind(t2[4], vehicle, rawType);
        rows.push({
          gtfs: BkkLib.gtfsTripGeo(idx, t2, oidx, didx),
          tripId: "gtfs:" + route + ":" + day.ymd + ":" + depMin + ":" + origin.i,
          label: route,
          color,
          text,
          vehicle: kind.vehicle,
          rawType: kind.rawType,
          dep: depTs,
          sched: depTs,
          delay: 0,
          head: dest && dest.name || destStop && destStop.name || "",
          platform: "",
          wheelchair: false,
          bikesAllowed: false,
          trainNumber: "",
          travel
        });
      }
    }
    rows.sort((a, b) => a.dep - b.dep);
    const uniq = [];
    const seen = /* @__PURE__ */ new Set();
    rows.forEach((row) => {
      const k = row.label + "|" + row.sched;
      if (seen.has(k)) return;
      seen.add(k);
      uniq.push(row);
    });
    return uniq.slice(0, rowCap);
  },
  mergeDestIndex(a, b) {
    const byKey = /* @__PURE__ */ new Map();
    const destRoutes = {};
    const add = (idx) => {
      (idx && idx.dests || []).forEach((d) => {
        if (!d || !d.key) return;
        if (!byKey.has(d.key)) byKey.set(d.key, d);
        if (!destRoutes[d.key]) destRoutes[d.key] = [];
        (idx.destRoutes && idx.destRoutes[d.key] || d.routes || []).forEach((rid) => {
          if (rid && destRoutes[d.key].indexOf(rid) < 0) destRoutes[d.key].push(rid);
        });
      });
    };
    add(a);
    add(b);
    const dests = Array.from(byKey.values());
    dests.sort((x, y) => x.name.localeCompare(y.name, "hu"));
    return { dests, destRoutes };
  },
  rowTrainNumber(row) {
    const direct = String(row && row.trainNumber || "").trim();
    if (direct) return direct.replace(/^0+/, "") || direct;
    return BkkLib.trainNumberFromTripId(row && row.tripId);
  },
  mergeVolanRows(gtfs, otp, limit, opts) {
    const out = [];
    const byTime = /* @__PURE__ */ new Map();
    const byNum = /* @__PURE__ */ new Map();
    const byLoose = /* @__PURE__ */ new Map();
    const trainNumberMatchWindowMin = 5;
    const generic = (c) => {
      const x = String(c || "").replace("#", "").toLowerCase();
      return !x || x === "4477aa";
    };
    const timeOf = (row) => `${String(row.label || "").toUpperCase()}|${Math.round(Number(row.sched || row.dep || 0) / 60)}`;
    const overlay = (prev, row) => {
      if (!prev || !row) return;
      const prevNum = String(prev.trainNumber || "").trim();
      const prevNumFromId = !!prevNum && BkkLib.isFutarTripId(prev.tripId) && prevNum === BkkLib.trainNumberFromTripId(prev.tripId);
      if (generic(prev.color) && row.color && !generic(row.color)) {
        prev.color = row.color;
        if (row.text) prev.text = row.text;
      }
      const prevLab = String(prev.label || "");
      const rowLab = String(row.label || "");
      const brandedLab = (lab) => {
        const s = String(lab || "").trim();
        return !!(s && !BkkLib.genericRailLabel(s) && !/^\d+$/.test(s));
      };
      if (!brandedLab(prevLab) && brandedLab(rowLab)) {
        prev.label = rowLab;
      }
      const prevHasGps = !!BkkLib.mapGpsLatLon(prev.lat, prev.lon);
      const rowHasGps = !!BkkLib.mapGpsLatLon(row.lat, row.lon);
      const rowElvira = /^elvira:/i.test(String(row.tripId || ""));
      if (rowHasGps && (!prevHasGps || rowElvira)) {
        prev.lat = Number(row.lat);
        prev.lon = Number(row.lon);
      }
      const tid = String(row.tripId || "");
      if (BkkLib.isFutarTripId(tid)) prev.tripId = tid;
      else if (tid && !/^(elvira:|gtfs:)/.test(tid) && !prevHasGps) prev.tripId = tid;
      if ((!Array.isArray(prev.shape) || prev.shape.length < 2) && Array.isArray(row.shape) && row.shape.length >= 2) {
        prev.shape = row.shape;
      }
      ["vehicleId", "licensePlate", "model", "vehicleStatus", "stopDistancePercent"].forEach((k) => {
        if ((prev[k] == null || prev[k] === "") && row[k] != null && row[k] !== "") prev[k] = row[k];
      });
      if (!prev.platform && row.platform) prev.platform = String(row.platform);
      if (row.booking) prev.booking = true;
      if (row.nationalPass) prev.nationalPass = true;
      if (row.reservationFreeSection) prev.reservationFreeSection = true;
      if (row.trainNumber && (!prevNum || rowElvira && prevNumFromId)) {
        prev.trainNumber = String(row.trainNumber);
      }
      if (prev.travel == null && row.travel != null) prev.travel = row.travel;
      if (row.wheelchair) prev.wheelchair = true;
      if (row.bikesAllowed) prev.bikesAllowed = true;
    };
    const minuteOf = (row) => Math.round(Number(row && (row.sched || row.dep) || 0) / 60);
    const numberKey = (number) => {
      const raw = String(number || "").trim();
      return BkkLib.emmaTrainNumber(raw) || raw.replace(/^0+/, "") || raw;
    };
    const registerNumber = (number, index, map = byNum) => {
      const key = numberKey(number);
      if (!key) return;
      const rows = map.get(key) || [];
      if (rows.indexOf(index) < 0) rows.push(index);
      map.set(key, rows);
    };
    const findNumberMatch = (number, minute, map = byNum) => {
      const rows = map.get(numberKey(number)) || [];
      if (!minute || !rows.length) return -1;
      let best = -1;
      let distance = trainNumberMatchWindowMin + 1;
      rows.forEach((index) => {
        const delta = Math.abs(minuteOf(out[index]) - minute);
        if (delta < distance) {
          best = index;
          distance = delta;
        }
      });
      return distance <= trainNumberMatchWindowMin ? best : -1;
    };
    const looseNumber = (row) => {
      if (!BkkLib.isTrainRow(row)) return "";
      if (!/^(IC|EC|IR|RJX)$/i.test(String(row.label || "").trim())) return "";
      const fromId = BkkLib.trainNumberFromTripId(row.tripId);
      const direct = String(row.trainNumber || "").trim();
      if (!fromId || direct && direct !== fromId) return "";
      return BkkLib.trainNumberLooseFromTripId(row.tripId);
    };
    const ownNumber = (row) => BkkLib.isTrainRow(row) && !!String(row.trainNumber || "").trim() && !BkkLib.isFutarTripId(row.tripId) && !BkkLib.suburbanLineLabel(row.label);
    const numbersOf = (row) => [BkkLib.rowTrainNumber(row), looseNumber(row)].map(numberKey).filter(Boolean);
    const otherTrain = (a, b) => {
      const x = numbersOf(a);
      const y = numbersOf(b);
      return !!x.length && !!y.length && !x.some((n) => y.indexOf(n) >= 0);
    };
    const brandedHost = (row) => BkkLib.isTrainRow(row) && !BkkLib.numberedRailBadge(row) && (BkkLib.suburbanLineLabel(row && row.label) || !BkkLib.genericRailLabel(row && row.label) && String(row && row.label || "").trim());
    const add = (row, skipUnmatched) => {
      if (!row || !departureStillDue(row.dep || row.sched)) return;
      const n = BkkLib.rowTrainNumber(row);
      const min = minuteOf(row);
      let numberMatch = n ? findNumberMatch(n, min) : -1;
      if (numberMatch < 0 && n && ownNumber(row)) numberMatch = findNumberMatch(n, min, byLoose);
      if (numberMatch >= 0) {
        overlay(out[numberMatch], row);
        return;
      }
      if (BkkLib.numberedRailBadge(row) && min) {
        const host = out.findIndex((p) => brandedHost(p) && minuteOf(p) === min && !otherTrain(p, row));
        if (host >= 0) {
          overlay(out[host], row);
          registerNumber(n, host);
          return;
        }
      } else if (brandedHost(row) && min) {
        const numbered = out.findIndex((p) => BkkLib.numberedRailBadge(p) && minuteOf(p) === min && !otherTrain(p, row));
        if (numbered >= 0) {
          overlay(out[numbered], row);
          registerNumber(n, numbered);
          return;
        }
      }
      if (!skipUnmatched && BkkLib.isTrainRow(row) && min) {
        const namedIc = (r) => BkkLib.isTrainRow(r) && !BkkLib.suburbanLineLabel(r && r.label) && !BkkLib.genericRailLabel(r && r.label) && !/^\d+$/.test(String(r && r.label || "").trim());
        const genericIc = (r) => BkkLib.isTrainRow(r) && BkkLib.genericRailLabel(r && r.label);
        let pair = -1;
        if (namedIc(row)) {
          pair = out.findIndex((p) => genericIc(p) && minuteOf(p) === min && !otherTrain(p, row));
        } else if (genericIc(row)) {
          pair = out.findIndex((p) => namedIc(p) && minuteOf(p) === min && !otherTrain(p, row));
        }
        if (pair >= 0) {
          overlay(out[pair], row);
          const pn = BkkLib.rowTrainNumber(out[pair]) || n;
          registerNumber(pn, pair);
          registerNumber(n, pair);
          return;
        }
      }
      const tk = timeOf(row);
      if (!skipUnmatched && byTime.has(tk) && !otherTrain(out[byTime.get(tk)], row)) {
        overlay(out[byTime.get(tk)], row);
        return;
      }
      if (skipUnmatched) return;
      const idx = out.length;
      out.push(row);
      byTime.set(tk, idx);
      registerNumber(n, idx);
      registerNumber(looseNumber(row), idx, byLoose);
    };
    const overlayOnly = !!(opts && opts.overlayOnly);
    const hosts = overlayOnly ? gtfs || [] : otp || [];
    const extra = overlayOnly ? otp || [] : gtfs || [];
    hosts.forEach((row) => add(row, false));
    extra.forEach((row) => add(row, overlayOnly));
    out.sort((a, b) => (a.dep || 0) - (b.dep || 0));
    const cap = Number(limit);
    return out.slice(0, Number.isFinite(cap) && cap > 0 ? cap : 12);
  }
});

// src/lib/services.js
Object.assign(BkkLib, {
  elviraRowsFromResult(res) {
    if (!res) return [];
    if (Array.isArray(res)) return res;
    if (Array.isArray(res.departures)) return res.departures;
    const payload = res.response || res.service_response;
    if (payload && Array.isArray(payload.departures)) return payload.departures;
    if (Array.isArray(payload)) return payload;
    return [];
  },
  /* The integration registers elvira_between; without it (an older or
     unloaded integration) every poll was one failing call_service. */
  hasElvira(hass) {
    const domain = hass && hass.services && hass.services.hungarian_transport;
    return !!(domain && typeof domain === "object" && domain.elvira_between);
  },
  /* After a failed call ELVIRA is left alone this long, and the outage is
     logged once, not on every poll of every MÁV card. */
  _elviraRetryMs: 3 * 6e4,
  _elviraDownUntil: 0,
  _elviraWarned: false,
  async elviraBetween(hass, originId, destId, minutesAfter, destName, opts) {
    opts = opts || {};
    if (!BkkLib.hasElvira(hass) || Date.now() < BkkLib._elviraDownUntil) return [];
    let origin = BkkLib.elviraStationCode(originId);
    let dest = BkkLib.elviraStationCode(destId);
    if (!origin) origin = await this.elviraResolveCode(opts.apiKey, originId, opts.originName || "");
    if (!dest) dest = await this.elviraResolveCode(opts.apiKey, destId, destName || "");
    if (!origin || !dest || origin === dest) {
      return [];
    }
    const serviceData = {
      origin_code: origin,
      dest_code: dest,
      minutes_after: minutesAfter || DEFAULT_MINUTES_AFTER,
      dest_name: destName || ""
    };
    const pair = `${origin}>${dest}`;
    try {
      const res = await BkkLib.callServiceResponse(hass, "hungarian_transport", "elvira_between", serviceData);
      BkkLib._elviraWarned = false;
      if (!res) return [];
      const payload = BkkLib.servicePayload(res);
      if (payload && payload.error === "elvira_rate_limited") return [];
      if (payload && payload.error) {
        BkkLib._elviraDown(pair, `${payload.error}${payload.detail ? ` (${payload.detail})` : ""}`);
        return [];
      }
      BkkLib._elviraOutages.delete(pair);
      const rows = BkkLib.elviraRowsFromResult(res);
      return Array.isArray(rows) ? rows : [];
    } catch (err) {
      BkkLib._elviraDownUntil = Date.now() + BkkLib._elviraRetryMs;
      if (!BkkLib._elviraWarned) {
        BkkLib._elviraWarned = true;
        console.warn("MÁV ELVIRA reservation data unavailable, retrying in a few minutes:", err);
      }
      return [];
    }
  },
  /* Cards poll every 15–45 s: warn when a station pair's ELVIRA outage
     starts, not on every poll; its next good answer ends the outage. */
  _elviraOutages: /* @__PURE__ */ new Set(),
  _elviraDown(pair, reason) {
    if (BkkLib._elviraOutages.has(pair)) return;
    BkkLib._elviraOutages.add(pair);
    console.warn("MÁV ELVIRA reservation data unavailable:", reason);
  },
  coachVehiclesFromResult(res) {
    if (!res) return [];
    if (Array.isArray(res.vehicles)) return res.vehicles;
    const payload = res.response || res.service_response;
    if (payload && Array.isArray(payload.vehicles)) return payload.vehicles;
    return [];
  },
  _coachPosInflight: null,
  _coachPosCache: null,
  async coachPositions(hass) {
    if (!BkkLib.hasCompanionService(hass, "coach_positions")) return [];
    const now = Date.now();
    const hit = BkkLib._coachPosCache;
    if (hit && now - hit.ts < 2e4) return hit.vehicles;
    if (BkkLib._coachPosInflight) return BkkLib._coachPosInflight;
    const run = (async () => {
      try {
        const res = await BkkLib.hassService(hass, "coach_positions", {});
        if (!res) return [];
        const vehicles = BkkLib.coachVehiclesFromResult(res);
        const list = Array.isArray(vehicles) ? vehicles : [];
        BkkLib._coachPosCache = { ts: Date.now(), vehicles: list };
        return list;
      } catch (err) {
        return [];
      } finally {
        BkkLib._coachPosInflight = null;
      }
    })();
    BkkLib._coachPosInflight = run;
    return run;
  },
  async coachShape(hass, route, headsign) {
    if (!route || !BkkLib.hasCompanionService(hass, "coach_shape")) return "";
    const serviceData = { route: String(route), headsign: String(headsign || "") };
    try {
      const res = await BkkLib.hassService(hass, "coach_shape", serviceData);
      if (!res) return "";
      const payload = res && (res.response || res.service_response) || res || {};
      return String(payload.points || "");
    } catch (err) {
      return "";
    }
  },
  servicePayload(res) {
    if (!res || typeof res !== "object") return {};
    if (Array.isArray(res.shape) || Array.isArray(res.points) || Array.isArray(res.departures)) return res;
    const nested = res.response || res.service_response || res.result;
    if (nested && nested !== res) return BkkLib.servicePayload(nested);
    return res;
  },
  /* Call a service that returns a response. The websocket message is the one
     path that carries return_response on every frontend version; callService
     is only the fallback when there is no connection. Resolves to null when
     neither exists. */
  async callServiceResponse(hass, domain, service, serviceData) {
    if (!hass) return null;
    if (hass.connection && typeof hass.connection.sendMessagePromise === "function") {
      return hass.connection.sendMessagePromise({
        type: "call_service",
        domain: String(domain),
        service: String(service),
        service_data: serviceData || {},
        return_response: true
      });
    }
    if (typeof hass.callService === "function") {
      return hass.callService(domain, service, serviceData || {}, void 0, false, true);
    }
    return null;
  },
  /* The bkk_stop companion integration is optional. Without it every poll
     with GTFS or MOTIS rows would make a failing call_service round trip, so
     its services are only called when hass lists them. */
  hasCompanionService(hass, service) {
    const domain = hass && hass.services && hass.services.bkk_stop;
    return !!(domain && typeof domain === "object" && domain[service]);
  },
  /* Companion bkk_stop services (coach GPS and shapes). Resolves to null
     when the companion is not installed or lacks the service. */
  hassService(hass, service, serviceData) {
    if (!BkkLib.hasCompanionService(hass, service)) return Promise.resolve(null);
    return BkkLib.callServiceResponse(hass, "bkk_stop", service, serviceData);
  },
  /* FUTÁR trip-details.json already ships the street polyline
     (8E Kelenföld-bound: ~1000 points, Újpalota → Kelenföld).
     bkk_stop has no trip_shape service, so the map must decode this
     itself instead of waiting on Home Assistant. */
  async futarTripShape(apiKey, cache, tripId) {
    if (!BkkLib.hasFutar(apiKey) || !BkkLib.isFutarTripId(tripId)) return [];
    try {
      const packed = await BkkLib.tripDetails(apiKey, cache, tripId);
      return BkkLib.validMapShape(BkkLib.decodePolyline(packed && packed.polyline));
    } catch (_e) {
      return [];
    }
  },
  /* Bus/tram/metro/trolley/coach maps want the published line, not the
     Transitous hop-cut. combineHopShapes drops a FUTÁR line that starts
     far from the board (8E at Újpalota vs a Keleti hop). */
  pickPublishedLineShape(motisShape, futarShape, row, coversHead) {
    const motis = BkkLib.validMapShape(motisShape);
    const futar = BkkLib.validMapShape(futarShape);
    if (BkkLib.keepsFullLineShape(row) && !BkkLib.isTrainRow(row) && futar.length >= 8) {
      return futar;
    }
    return BkkLib.combineHopShapes(motis, futar, coversHead, []);
  }
});

// src/lib/departures.js
Object.assign(BkkLib, {
  async addTransitousVolanStops(byId, q, motisHits) {
    const motis = motisHits || await BkkLib.searchStopsTransitous(q);
    let idx = null;
    try {
      idx = await this.volanIndex();
    } catch (_e) {
      idx = null;
    }
    motis.forEach((s) => {
      if (!s || !s.id || !/^hu-volanbusz_/i.test(s.id)) return;
      let id = s.id;
      let name = s.name || "";
      if (idx) {
        const hit = BkkLib.volanMatchStop(idx, { id: s.id, name: s.name }, null);
        if (hit && hit.id) {
          id = hit.id;
          name = hit.name || name;
        }
      }
      if (!id || byId.has(id)) return;
      const num = BkkLib.stopNum(id) || BkkLib.stopNum(s.id);
      if (num) {
        const clash = Array.from(byId.values()).find((x) => BkkLib.stopNum(x.id) === num);
        if (clash) return;
      }
      byId.set(id, {
        id,
        name,
        label: name,
        lat: s.lat,
        lon: s.lon
      });
    });
  },
  async searchStops(apiKey, q, mode, city, opts) {
    const byId = /* @__PURE__ */ new Map();
    if (mode === "volan" || mode === "all") {
      try {
        (await this.volanSearchStops(q)).forEach((s) => byId.set(s.id, s));
      } catch (_e) {
      }
      try {
        if (!(opts && opts.skipTransitousVolan)) {
          await this.addTransitousVolanStops(byId, q);
        }
      } catch (_e) {
      }
    }
    if (mode === "all") {
      try {
        const idx = await this.cityIndex();
        const seenFold = /* @__PURE__ */ new Set();
        BkkLib.citySearchAll(idx, q).forEach((s) => {
          const opId = BkkLib.cityOpId(idx, s);
          const op = (idx.ops || []).find((o) => o.id === opId);
          const city2 = op ? String(op.name).split(" — ")[0] : "";
          const fold = BkkLib.fold(s.name) + "|" + opId;
          if (!opId || seenFold.has(fold)) return;
          seenFold.add(fold);
          byId.set(s.id, {
            id: s.id,
            name: s.name,
            label: city2 ? s.name + " (" + city2 + ")" : s.name,
            city: true
          });
        });
      } catch (_e) {
      }
    }
    if (mode === "helyi") {
      try {
        (await this.citySearchStops(q, city)).forEach((s) => byId.set(s.id, s));
      } catch (_e) {
      }
      return BkkLib.groupStops(Array.from(byId.values())).slice(0, 20);
    }
    if (BkkLib.hasFutar(apiKey)) {
      try {
        const queries = [q].concat(BkkLib.aliasQueries(q));
        for (let i = 0; i < queries.length; i++) {
          const data = await BkkLib.fetch(apiKey, "search.json", { query: queries[i] }, true);
          const stops = ((data.data || {}).references || {}).stops || {};
          Object.values(stops).forEach((s) => {
            if (!s || !s.id || !s.name) return;
            if (/^STOP_/i.test(String(s.id))) return;
            if (mode === "mav" && !BkkLib.isMavStop(s)) return;
            if (mode === "volan" && !BkkLib.isVolanStop(s)) return;
            if (mode === "bkk" && !BkkLib.isBkkStop(s)) return;
            const num = BkkLib.stopNum(s.id);
            if ((mode === "volan" || mode === "all") && num && BkkLib.isVolanStop(s)) {
              const clash = Array.from(byId.values()).find((x) => BkkLib.stopNum(x.id) === num);
              if (clash) return;
            }
            byId.set(s.id, s);
          });
        }
      } catch (err) {
        if (mode !== "volan" && mode !== "all" || !byId.size) throw err;
      }
    }
    let hits;
    if (mode === "all") {
      const mav = [];
      const volan = [];
      const city2 = [];
      const rest = [];
      Array.from(byId.values()).forEach((s) => {
        if (s.city) city2.push(s);
        else if (BkkLib.isMavStop(s)) mav.push(s);
        else if (BkkLib.isVolanStop(s)) volan.push(s);
        else rest.push(s);
      });
      const groups = [
        BkkLib.groupStops(rest),
        BkkLib.groupStops(mav),
        BkkLib.groupStops(volan),
        city2
      ];
      hits = [];
      const max = Math.max(groups[0].length, groups[1].length, groups[2].length, groups[3].length);
      for (let i = 0; i < max; i++) {
        groups.forEach((g) => {
          if (g[i]) hits.push(g[i]);
        });
      }
    } else {
      hits = BkkLib.groupStops(Array.from(byId.values()));
    }
    hits = hits.map((h) => {
      if (h.city && h.label) return h;
      let label = BkkLib.labelAliased(q, h);
      if (mode === "all") {
        if (BkkLib.isMavStop(h)) label += " (MÁV)";
        else if (BkkLib.isVolanStop(h)) label += " (Volán)";
      }
      return Object.assign({}, h, { label });
    });
    const foldedQ = BkkLib.fold(q);
    hits.sort((a, b) => {
      const as = foldedQ.includes("arpad hid") && BkkLib.fold(a.name).includes("goncz arpad") ? 0 : 1;
      const bs = foldedQ.includes("arpad hid") && BkkLib.fold(b.name).includes("goncz arpad") ? 0 : 1;
      if (as !== bs) return as - bs;
      return (a.label || a.name).localeCompare(b.label || b.name, "hu");
    });
    if (mode === "all") hits = BkkLib.collapseSameNameStops(hits);
    const cities = hits.filter((h) => h.city).slice(0, 40);
    const other = hits.filter((h) => !h.city).slice(0, 20);
    return cities.concat(other);
  },
  async remainingNames(apiKey, cache, stop, routeId) {
    const { variants, stops } = await BkkLib.loadRoutePattern(apiKey, cache, routeId);
    const names = [];
    const seen = /* @__PURE__ */ new Set();
    variants.forEach((v) => {
      const idx = (v.ids || []).findIndex((id) => BkkLib.stopMatchesOrigin(Object.assign({ id }, stops[id] || {}), stop));
      if (idx < 0) return;
      v.ids.slice(idx + 1).forEach((id) => {
        const raw = (stops[id] || {}).name;
        const n = BkkLib.placeNameKey({ name: raw }) || BkkLib.stationKey(raw) || BkkLib.norm(raw);
        if (!raw || !n || seen.has(n)) return;
        seen.add(n);
        names.push({ key: n, name: raw, id });
      });
    });
    return names;
  },
  originIndex(sts, stops, origin) {
    if (!origin) return 0;
    const oid = origin.id || origin;
    const on = origin.name || "";
    const originObj = { id: oid, name: on };
    let idx = (sts || []).findIndex((s) => BkkLib.stopMatchesOrigin(Object.assign({ id: s.stopId }, stops[s.stopId] || {}), originObj));
    return idx;
  },
  stopMatchesOrigin(stop, origin) {
    if (!stop || !origin) return false;
    const oid = origin.id || origin;
    const oname = origin.name || "";
    if (stop.id && BkkLib.sameStop(stop.id, oid)) return true;
    if (stop.parentStationId && BkkLib.sameStop(stop.parentStationId, oid)) return true;
    if (oname && BkkLib.nameEq(stop.name, oname)) return true;
    return false;
  },
  /* Parent stop-areas (BKK_CSF…) cap live departures at ~60 rows, which is
     only ~15 min at Keleti. Query the child poles that actually serve the
     selected routes so minutesAfter can fill. */
  async loadRoutePattern(apiKey, cache, routeId) {
    const hit = BkkLib._cached(cache, routeId);
    if (hit && hit.variants) return hit;
    if (BkkLib._inflight[routeId]) return BkkLib._inflight[routeId];
    const p = (async () => {
      try {
        const data = await BkkLib.fetch(apiKey, "route-details.json", {
          routeId,
          includeReferences: "true"
        }, true);
        const entry = (data.data || {}).entry || {};
        const packed = {
          variants: (entry.variants || []).map((v) => ({
            name: v.name,
            ids: v.stopIds || []
          })),
          stops: ((data.data || {}).references || {}).stops || {}
        };
        return BkkLib._store(cache, routeId, packed);
      } finally {
        delete BkkLib._inflight[routeId];
      }
    })();
    BkkLib._inflight[routeId] = p;
    return p;
  },
  polesFromPatterns(patterns, origin, destKey) {
    const poles = /* @__PURE__ */ new Set();
    (patterns || []).forEach((pat) => {
      if (!pat) return;
      const variants = pat.variants || [];
      const stops = pat.stops || {};
      variants.forEach((v) => {
        const ids = v.ids || [];
        const oidx = ids.findIndex((id) => BkkLib.stopMatchesOrigin(Object.assign({ id }, stops[id] || {}), origin));
        if (oidx < 0) return;
        if (destKey) {
          const hits = ids.slice(oidx + 1).some((id) => BkkLib.nameEq((stops[id] || {}).name, destKey));
          if (!hits) return;
        }
        poles.add(ids[oidx]);
      });
    });
    return Array.from(poles);
  },
  /* Poles of the loaded route patterns that belong to the stop area parentId. */
  childPolesOf(cache, routeIds, parentId) {
    const kids = /* @__PURE__ */ new Set();
    (routeIds || []).forEach((rid) => {
      const stops = (BkkLib._cached(cache, rid) || {}).stops || {};
      Object.values(stops).forEach((st) => {
        if (st && st.id && st.parentStationId && BkkLib.sameStop(st.parentStationId, parentId)) kids.add(st.id);
      });
    });
    return kids;
  },
  async originPoles(apiKey, cache, origin, routeIds, destKey) {
    const routes = (routeIds || []).filter(Boolean);
    const patterns = await Promise.all(routes.map(async (rid) => {
      try {
        return await BkkLib.loadRoutePattern(apiKey, cache, rid);
      } catch (_e) {
        return null;
      }
    }));
    const poles = new Set(BkkLib.polesFromPatterns(patterns, origin, destKey || ""));
    if (!destKey) {
      if (!poles.size && origin && BkkLib.isStopArea({ id: origin.id })) {
        (await BkkLib.childStopIds(apiKey, cache, origin.id)).forEach((id) => poles.add(id));
      }
      if (!poles.size && origin && origin.id) poles.add(origin.id);
    }
    return Array.from(poles);
  },
  async childStopIds(apiKey, cache, parentId) {
    const key = "children:" + parentId;
    const hit = BkkLib._cached(cache, key);
    if (Array.isArray(hit)) return hit;
    if (BkkLib._inflight[key]) return BkkLib._inflight[key];
    const p = (async () => {
      try {
        const data = await BkkLib.fetch(apiKey, "schedule-for-stop.json", {
          stopId: parentId,
          includeReferences: "true"
        }, true);
        const stops = ((data.data || {}).references || {}).stops || {};
        const ids = Object.values(stops).filter((s) => s && s.id && s.parentStationId === parentId).filter((s) => !/^STOP_/i.test(s.id) && !BkkLib.isMavStop(s) && !BkkLib.isVolanStop(s)).map((s) => s.id);
        return BkkLib._store(cache, key, ids);
      } finally {
        delete BkkLib._inflight[key];
      }
    })();
    BkkLib._inflight[key] = p;
    return p;
  },
  /* A dashboard left open for a day would otherwise accumulate every trip it
     ever looked at, each with its full stop list. Oldest entries go first. */
  cachePut(cache, key, value) {
    if (!cache) return value;
    const keys = Object.keys(cache);
    if (keys.length >= 400) {
      keys.slice(0, keys.length - 399).forEach((k) => {
        delete cache[k];
      });
    }
    cache[key] = value;
    return value;
  },
  async tripDetails(apiKey, cache, tripId) {
    const key = "trip:" + tripId;
    const hit = BkkLib._cached(cache, key);
    if (hit) return hit;
    if (BkkLib._inflight[key]) return BkkLib._inflight[key];
    const p = (async () => {
      try {
        const data = await BkkLib.fetch(apiKey, "trip-details.json", { tripId });
        const packed = {
          sts: ((data.data || {}).entry || {}).stopTimes || [],
          stops: ((data.data || {}).references || {}).stops || {},
          polyline: (((data.data || {}).entry || {}).polyline || {}).points || "",
          vehicle: ((data.data || {}).entry || {}).vehicle || null,
          nowSec: BkkLib.unixSec(data.currentTime)
        };
        return BkkLib._store(cache, key, packed);
      } finally {
        delete BkkLib._inflight[key];
      }
    })();
    BkkLib._inflight[key] = p;
    return p;
  },
  tripReaches(details, origin, destKey) {
    const { sts, stops } = details;
    const oidx = BkkLib.originIndex(sts, stops, origin);
    if (oidx < 0) return false;
    return sts.slice(oidx + 1).some((s) => BkkLib.nameEq((stops[s.stopId] || {}).name, destKey));
  },
  async tripGoesTo(apiKey, cache, tripId, origin, destKey) {
    if (!tripId || !destKey) return false;
    try {
      return BkkLib.tripReaches(await BkkLib.tripDetails(apiKey, cache, tripId), origin, destKey);
    } catch (_e) {
      return false;
    }
  },
  /* What tripGoesTo would answer from the cached trip details alone:
     true / false, or undefined when the trip was never loaded. */
  knownTripGoesTo(cache, tripId, origin, destKey) {
    if (!tripId || !destKey) return false;
    const hit = BkkLib._cached(cache, "trip:" + tripId);
    return hit ? BkkLib.tripReaches(hit, origin, destKey) : void 0;
  },
  /* The trip's own stops for the map: where the rider boards, and the first
     later stop named like the card's destination. Exact, and on the route. */
  async tripHopStops(apiKey, cache, tripId, origin, destKey) {
    const { sts, stops } = await BkkLib.tripDetails(apiKey, cache, tripId);
    const oidx = BkkLib.originIndex(sts, stops, origin);
    if (oidx < 0) return null;
    const at = (s) => {
      const stop = s && stops[s.stopId] || {};
      return BkkLib.mapGpsLatLon(stop.lat, stop.lon);
    };
    const hit = destKey ? sts.slice(oidx + 1).find((s) => BkkLib.nameEq((stops[s.stopId] || {}).name, destKey)) : null;
    return { board: at(sts[oidx]), alight: hit ? at(hit) : null };
  },
  async tripRemainingNames(apiKey, cache, tripId, origin) {
    const { sts, stops } = await BkkLib.tripDetails(apiKey, cache, tripId);
    const oidx = BkkLib.originIndex(sts, stops, origin);
    if (oidx < 0) return [];
    const names = [];
    const seen = /* @__PURE__ */ new Set();
    sts.slice(oidx + 1).forEach((s) => {
      const raw = (stops[s.stopId] || {}).name;
      const n = BkkLib.placeNameKey({ name: raw }) || BkkLib.stationKey(raw) || BkkLib.norm(raw);
      if (!raw || !n || seen.has(n)) return;
      seen.add(n);
      names.push({ key: n, name: raw, id: s.stopId });
    });
    return names;
  },
  async reachableDests(apiKey, cache, stop, mode, city) {
    if (mode === "helyi") {
      return this.cityReachableDests(stop, city);
    }
    let out = { dests: [], destRoutes: {} };
    try {
      const data = await BkkLib.fetch(apiKey, "arrivals-and-departures-for-stop.json", {
        stopId: stop.id,
        minutesAfter: DEPART_MINUTES_AFTER,
        minutesBefore: "0",
        onlyDepartures: "true",
        includeReferences: "true"
      }, true);
      const refs = (data.data || {}).references || {};
      const routes = refs.routes || {};
      const trips = refs.trips || {};
      const times = ((data.data || {}).entry || {}).stopTimes || [];
      const seenTrip = /* @__PURE__ */ new Set();
      const seenRoute = /* @__PURE__ */ new Set();
      const tripJobs = [];
      const routeIds = [];
      for (let i = 0; i < times.length; i++) {
        const st = times[i];
        const trip = trips[st.tripId] || {};
        const rid = trip.routeId;
        const rt = routes[rid] || { id: rid, type: "" };
        if (!BkkLib.routeMatchesMode(rt, mode)) continue;
        if (rid && !seenRoute.has(rid)) {
          seenRoute.add(rid);
          routeIds.push(rid);
        }
        if (st.tripId && !seenTrip.has(st.tripId) && tripJobs.length < 40) {
          seenTrip.add(st.tripId);
          tripJobs.push({ tripId: st.tripId, routeId: rid });
        }
      }
      const fromTrips = await Promise.all(tripJobs.map(async (job) => {
        try {
          const names = await BkkLib.tripRemainingNames(apiKey, cache, job.tripId, stop);
          return { routeId: job.routeId, names };
        } catch (_e) {
          return { routeId: job.routeId, names: [] };
        }
      }));
      const fromRoutes = await Promise.all(routeIds.slice(0, 32).map(async (rid) => {
        try {
          const names = await BkkLib.remainingNames(apiKey, cache, stop, rid);
          return { routeId: rid, names };
        } catch (_e) {
          return { routeId: rid, names: [] };
        }
      }));
      const byKey = /* @__PURE__ */ new Map();
      const destRoutes = {};
      fromTrips.concat(fromRoutes).forEach(({ routeId, names }) => {
        (names || []).forEach((n) => {
          if (!n || !n.key) return;
          const prev = byKey.get(n.key);
          if (!prev) byKey.set(n.key, n);
          else if (BkkLib.isMetroSuffixName(prev.name) && !BkkLib.isMetroSuffixName(n.name)) {
            byKey.set(n.key, n);
          }
          if (!destRoutes[n.key]) destRoutes[n.key] = [];
          if (routeId && destRoutes[n.key].indexOf(routeId) < 0) destRoutes[n.key].push(routeId);
        });
      });
      const dests = Array.from(byKey.values());
      dests.sort((a, b) => a.name.localeCompare(b.name, "hu"));
      out = { dests, destRoutes };
    } catch (err) {
      if (mode !== "volan" && mode !== "all") throw err;
    }
    if (mode === "volan" || mode === "all") {
      try {
        out = BkkLib.mergeDestIndex(await this.volanReachableDests(stop), out);
      } catch (_e) {
      }
    }
    if (mode === "all") {
      try {
        const idx = await this.cityIndex();
        const cityId = BkkLib.cityOpId(idx, stop);
        if (cityId) out = BkkLib.mergeDestIndex(await this.cityReachableDests(stop, cityId), out);
      } catch (_e) {
      }
    }
    return out;
  },
  async travelMin(apiKey, tripId, destKey, dep, origin, cache) {
    if (!tripId || !destKey || /^(gtfs:|elvira:|motis:)/.test(String(tripId))) return null;
    try {
      const { sts, stops } = await BkkLib.tripDetails(apiKey, cache || {}, tripId);
      const oidx = BkkLib.originIndex(sts, stops, origin);
      const start = oidx >= 0 ? oidx + 1 : 0;
      const dest = sts.slice(start).find((s) => BkkLib.nameEq((stops[s.stopId] || {}).name, destKey));
      if (!dest) return null;
      const o = oidx >= 0 ? sts[oidx] : null;
      const oPred = o && (o.predictedDepartureTime || o.predictedArrivalTime);
      const oPlan = o && (o.departureTime || o.arrivalTime);
      let from = dep;
      let arr = dest.predictedArrivalTime || dest.arrivalTime || dest.departureTime;
      if (oPred && dest.predictedArrivalTime) {
        from = oPred;
        arr = dest.predictedArrivalTime;
      } else if (oPlan && (dest.arrivalTime || dest.departureTime)) {
        from = oPlan;
        arr = dest.arrivalTime || dest.departureTime;
      }
      if (!arr || !from) return null;
      const mins = Math.round((arr - from) / 60);
      return mins >= 1 && mins <= 1440 ? mins : null;
    } catch (_e) {
      return null;
    }
  },
  async departures(apiKey, stopId, routeIds, dest, opts) {
    opts = opts || {};
    const mode = opts.mode || (opts.mav ? "mav" : "bkk");
    const horizon = clampMinutesAfter(opts.minutesAfter);
    if (mode === "helyi") {
      let cityRows = await this.cityDepartures(stopId, dest, opts.originName, opts.city, horizon);
      cityRows = await BkkLib.fillTransitousIfEmpty(
        cityRows,
        { id: stopId, name: opts.originName || "" },
        dest,
        opts
      );
      if (opts.deferGps) return cityRows;
      return BkkLib.attachCoachGps(opts.hass, cityRows);
    }
    const cache = opts.cache || {};
    const origin = { id: stopId, name: opts.originName || "" };
    const selected = new Set(routeIds || []);
    const destKey = dest && dest.key;
    const maxRows = maxDepartureRows(horizon);
    const now = Math.floor(Date.now() / 1e3);
    const latest = now + horizon * 60;
    let rows = [];
    let elviraExtra = [];
    if ((mode === "mav" || mode === "all") && opts.hass && !opts.skipElvira) {
      try {
        const destId = dest && dest.id || opts.destStopId || "";
        elviraExtra = await this.elviraBetween(
          opts.hass,
          stopId,
          destId,
          horizon,
          dest && dest.name || "",
          { apiKey, originName: opts.originName || "" }
        );
      } catch (_e) {
      }
    }
    const queryFutar = mode === "bkk" || mode === "mav" || /^BKK_/i.test(String(stopId || ""));
    const futarTask = queryFutar ? (async () => {
      let candidates = [];
      try {
        let poles = [];
        const poleRoutes = Array.from(selected);
        if (poleRoutes.length) {
          try {
            poles = await BkkLib.originPoles(apiKey, cache, origin, poleRoutes, destKey || "");
          } catch (_e) {
            poles = [];
          }
        }
        let near = [];
        if (!poles.length) {
          poles = [stopId];
        } else if (destKey) {
          const kids = BkkLib.childPolesOf(cache, poleRoutes, stopId);
          const saved = poles.filter((p) => p !== stopId);
          near = saved.filter((p) => kids.has(p));
          poles = [stopId].concat(saved.filter((p) => !kids.has(p)));
        }
        let boardErr = null;
        const loadBoards = (ids) => Promise.all(ids.map(async (pid) => {
          try {
            return await BkkLib.departureBoard(apiKey, pid, horizon);
          } catch (err) {
            if (!boardErr) boardErr = err;
            return null;
          }
        }));
        let payloads = await loadBoards(poles);
        if (near.length && !payloads[0]) {
          poles = poles.concat(near);
          payloads = payloads.concat(await loadBoards(near));
          near = [];
        }
        if (boardErr && payloads.every((data) => !data)) throw boardErr;
        const usedParentOnly = poles[0] === stopId && (poles.length === 1 || !!destKey);
        const span = payloads.slice(0, usedParentOnly ? 1 : payloads.length).reduce((best, data) => {
          const cur = BkkLib.boardSpanMin(data, now);
          return {
            count: best.count + cur.count,
            spanMin: Math.max(best.spanMin, cur.spanMin)
          };
        }, { count: 0, spanMin: 0 });
        const capped = usedParentOnly && (span.count >= 60 || span.spanMin + 2 < horizon);
        if (capped) {
          const discovered = [];
          payloads.forEach((data) => {
            const refs = (data && data.data || {}).references || {};
            const entry = (data && data.data || {}).entry || {};
            const trips = refs.trips || {};
            const routes = refs.routes || {};
            const ids = new Set(entry.routeIds || []);
            (entry.stopTimes || []).forEach((st) => {
              const rid = (trips[st.tripId] || {}).routeId;
              if (rid) ids.add(rid);
            });
            ids.forEach((rid) => {
              const rt = routes[rid] || {};
              if (!BkkLib.routeMatchesMode(rt, mode)) return;
              if (!BkkLib.keepFutarCandidate(rt, selected, mode, destKey)) return;
              if (discovered.indexOf(rid) < 0) discovered.push(rid);
            });
          });
          let extra = [];
          if (discovered.length) {
            try {
              extra = await BkkLib.originPoles(apiKey, cache, origin, discovered, destKey || "");
            } catch (_e) {
              extra = [];
            }
          }
          const fresh = Array.from(new Set(near.concat(extra))).filter((id) => poles.indexOf(id) < 0);
          if (fresh.length) {
            poles = poles.concat(fresh);
            payloads = payloads.concat(await loadBoards(fresh));
          }
        }
        const seenTrip = /* @__PURE__ */ new Set();
        payloads.forEach((data) => {
          if (!data) return;
          const refs = (data.data || {}).references || {};
          const routes = refs.routes || {};
          const trips = refs.trips || {};
          const entry = (data.data || {}).entry || {};
          const times = entry.stopTimes || [];
          const stops = refs.stops || {};
          times.forEach((st) => {
            if (!st.tripId || seenTrip.has(st.tripId)) return;
            const trip = trips[st.tripId] || {};
            const rid = trip.routeId;
            const rt = routes[rid] || {};
            if (!BkkLib.routeMatchesMode(rt, mode)) return;
            if (!BkkLib.keepFutarCandidate(rt, selected, mode, destKey)) return;
            const dep = st.predictedDepartureTime || st.departureTime;
            if (dep && dep > latest + 60) return;
            if (dep && !departureStillDue(dep, now)) return;
            seenTrip.add(st.tripId);
            const fallbackColor = mode === "volan" ? "F9AB13" : "4477aa";
            const fallbackText = mode === "volan" ? "000000" : "ffffff";
            const veh = st.vehicle || {};
            const loc = BkkLib.vehicleLoc(veh);
            const vehicle = BkkLib.vehicleKind(rt);
            candidates.push({
              tripId: st.tripId,
              label: rt.iconDisplayText || rt.shortName || "?",
              color: "#" + String(rt.color || fallbackColor).replace("#", ""),
              text: "#" + String(rt.textColor || fallbackText).replace("#", ""),
              vehicle,
              rawType: String(rt.type || "BUS").toUpperCase(),
              dep,
              sched: st.departureTime,
              delay: st.predictedDepartureTime && st.departureTime ? st.predictedDepartureTime - st.departureTime : 0,
              head: trip.tripHeadsign || st.stopHeadsign || "",
              /* A single pole's board leaves stopId off its stop times (the
                 entry names the pole), so its rows need the entry's stopId to
                 show the same stand code as the parent station's board. */
              platform: (stops[st.stopId || entry.stopId] || {}).platformCode || "",
              wheelchair: trip.wheelchairAccessible === 1 || trip.wheelchairAccessible === true || st.wheelchairAccessible === true,
              bikesAllowed: trip.bikesAllowed === 1 || trip.bikesAllowed === true,
              /* Only trains have a train number. A bus on a numeric ad-hoc
                 trip id (BKK_1792439004037) must not show one. */
              trainNumber: vehicle === "rail" ? trip.shortName || trip.tripShortName || BkkLib.trainNumberFromTripId(st.tripId) || "" : "",
              booking: !!st.mayRequireBooking,
              lat: loc.lat,
              lon: loc.lon,
              vehicleId: veh.vehicleId || "",
              licensePlate: veh.licensePlate || "",
              model: veh.model || "",
              vehicleStatus: veh.status || "",
              stopDistancePercent: veh.stopDistancePercent
            });
          });
        });
        candidates.sort((a, b) => (a.dep || 0) - (b.dep || 0));
      } catch (err) {
        if (mode !== "volan" && mode !== "all") throw err;
      }
      let sure = [];
      let maybe = [];
      const headHitsDest = (row) => !!(dest && (BkkLib.nameEq(row.head, dest.name) || BkkLib.nameEq(row.head, dest.key)));
      if (destKey && candidates.length) {
        candidates.forEach((row) => {
          const known = headHitsDest(row) || BkkLib.knownTripGoesTo(cache, row.tripId, origin, destKey);
          if (known === true) sure.push(row);
          else if (known === void 0) maybe.push(row);
        });
        sure = sure.slice(0, maxRows);
      } else {
        sure = candidates.slice(0, maxRows);
      }
      const asFutar = (list) => list.map((row) => Object.assign({ travel: null }, row));
      const early = asFutar(sure);
      const sureFull = sure.length >= maxRows;
      const lastSure = sureFull ? sure[sure.length - 1].dep || 0 : Infinity;
      const pool = maybe.filter((row) => (row.dep || 0) <= lastSure);
      if (destKey && pool.length && typeof opts.armThroughCheck === "function") {
        opts.armThroughCheck(() => {
          const pending = (async () => {
            const picked = sure.slice();
            let found = 0;
            for (let i = 0; i < pool.length && (sureFull ? found < maxRows : picked.length < maxRows); i += 16) {
              const chunk = pool.slice(i, i + 16);
              const flags = await Promise.all(chunk.map((row) => BkkLib.tripGoesTo(apiKey, cache, row.tripId, origin, destKey)));
              flags.forEach((ok, j) => {
                if (!ok) return;
                picked.push(chunk[j]);
                found += 1;
              });
            }
            picked.sort((a, b) => (a.dep || 0) - (b.dep || 0));
            const full = asFutar(picked.slice(0, maxRows));
            if (typeof opts.onFutarUpdate === "function") opts.onFutarUpdate(full);
          })();
          pending.catch(() => {
            if (typeof opts.onFutarUpdate === "function") opts.onFutarUpdate(asFutar(sure));
          });
        });
      }
      return early;
    })() : Promise.resolve([]);
    const gtfsTask = (async () => {
      let extraVolan = [];
      let extraCity = [];
      if (mode === "volan" || mode === "all") {
        try {
          extraVolan = await this.volanDepartures(stopId, dest, opts.originName, horizon);
          if (mode === "all" && selected.size) {
            extraVolan = extraVolan.filter((row) => selected.has("gtfs:" + row.label));
          }
        } catch (err) {
          if (mode !== "all") throw err;
        }
      }
      if (mode === "all") {
        try {
          const idx = await this.cityIndex();
          const cityId = BkkLib.cityOpId(idx, { id: stopId, name: opts.originName || "" });
          if (cityId) {
            extraCity = await this.cityDepartures(stopId, dest, opts.originName, cityId, horizon);
          }
        } catch (_e) {
        }
      }
      if ((extraVolan.length || extraCity.length) && typeof opts.onPartial === "function") {
        let partial = BkkLib.mergeVolanRows(extraVolan, rows, maxRows);
        partial = BkkLib.mergeVolanRows(extraCity, partial, maxRows);
        opts.onPartial(partial);
      }
      return { extraVolan, extraCity };
    })();
    const [futar, gtfs] = await Promise.all([futarTask, gtfsTask]);
    rows = BkkLib.mergeVolanRows(futar, rows, maxRows);
    rows = BkkLib.mergeVolanRows(gtfs.extraVolan, rows, maxRows);
    rows = BkkLib.mergeVolanRows(gtfs.extraCity, rows, maxRows);
    if (!opts.skipElvira) {
      rows = await BkkLib.fillTransitousIfEmpty(rows, origin, dest, opts);
    }
    if (elviraExtra.length) {
      rows = BkkLib.mergeVolanRows(rows, elviraExtra, maxRows, { overlayOnly: true });
    }
    if (opts.deferGps) return rows;
    return BkkLib.attachCoachGps(opts.hass, rows);
  }
});

// src/lib/vehicles.js
Object.assign(BkkLib, {
  trainNumberFromTripId(tripId) {
    const s = String(tripId || "");
    const long = s.match(/^BKK_([89]\d{3})1(?:_|$)/);
    if (long) return long[1];
    const ic = s.match(/^BKK_([89]\d{2})1(?:_|$)/);
    if (ic) return ic[1];
    const m = s.match(/^BKK_(\d{1,5})(?:_|$)/);
    return m ? m[1] : "";
  },
  /* The other reading of a 4–5 digit id body: the train number plus one
     digit, where the rule above kept every digit (BKK_8624_67 is IC 862,
     BKK_2461_311 is EC 246). '' when both readings agree. Ambiguous on its
     own: mergeVolanRows tries it only for long-distance rows, within its
     train-number window, against a number from another source. */
  trainNumberLooseFromTripId(tripId) {
    const m = String(tripId || "").match(/^BKK_(\d{4,5})(?:_|$)/);
    if (!m) return "";
    const loose = m[1].slice(0, -1);
    return loose === BkkLib.trainNumberFromTripId(tripId) ? "" : loose;
  },
  isFutarTripId(tripId) {
    const t2 = String(tripId || "");
    return !!t2 && !/^(elvira:|gtfs:|motis:)/.test(t2);
  },
  emmaTrainNumber(text) {
    const s = String(text || "").trim();
    const lead = s.match(/^(\d+)/);
    if (lead) return lead[1].replace(/^0+/, "") || lead[1];
    const mid = s.match(/\b(\d{3,5})\b/);
    return mid ? mid[1].replace(/^0+/, "") || mid[1] : "";
  },
  /* What a vehicle is matched on, worked out once per vehicle rather than
     once per row and vehicle pair. */
  _emmaVehicleKey(vehicle) {
    const trip = vehicle.trip || {};
    return {
      n: BkkLib.emmaTrainNumber(trip.tripShortName || vehicle.label || ""),
      blob: [
        trip.tripShortName,
        trip.tripHeadsign,
        (trip.route || {}).longName,
        (trip.route || {}).shortName,
        vehicle.label
      ].join(" ").toUpperCase()
    };
  },
  /* A row's train number, and its named label as a whole-word pattern
     (BALATON, not Balatonfüred): one RegExp per row. */
  _emmaRowKey(row) {
    const name = String(row.label || "").trim().toUpperCase();
    const named = !!name && name.length >= 3 && !BkkLib.genericRailLabel(name);
    const esc = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return {
      num: BkkLib.rowTrainNumber(row),
      re: named ? new RegExp("(?:^|[^A-Z0-9])" + esc + "(?:[^A-Z0-9]|$)") : null
    };
  },
  _emmaKeysMatch(rk, vk) {
    if (rk.num && vk.n && rk.num === vk.n) return true;
    if (!rk.re || !rk.re.test(vk.blob)) return false;
    return !rk.num || !vk.n;
  },
  matchEmmaVehicle(row, vehicle) {
    if (!row || !vehicle) return false;
    return BkkLib._emmaKeysMatch(BkkLib._emmaRowKey(row), BkkLib._emmaVehicleKey(vehicle));
  },
  _emmaKeyed(vehicles) {
    return (Array.isArray(vehicles) ? vehicles : []).filter(Boolean).map((v) => ({ v, k: BkkLib._emmaVehicleKey(v) }));
  },
  _applyEmmaKeyed(list, keyed) {
    if (!keyed.length) return list;
    list.forEach((row) => {
      if (!row) return;
      const rk = BkkLib._emmaRowKey(row);
      for (let i = 0; i < keyed.length; i++) {
        const v = keyed[i].v;
        if (!BkkLib._emmaKeysMatch(rk, keyed[i].k)) continue;
        const gps = BkkLib.mapGpsLatLon(v.lat, v.lon);
        if (!gps) continue;
        row.lat = gps[0];
        row.lon = gps[1];
        if (v.heading != null) row.heading = v.heading;
        if (v.speed != null) row.speed = v.speed;
        break;
      }
    });
    return list;
  },
  applyEmmaPositions(rows, vehicles) {
    const list = Array.isArray(rows) ? rows : [];
    return BkkLib._applyEmmaKeyed(list, BkkLib._emmaKeyed(vehicles));
  },
  coachRouteKeys(route) {
    const raw = String(route || "").trim().toUpperCase();
    const keys = [];
    const add = (value) => {
      const key = String(value || "");
      if (key && keys.indexOf(key) < 0) keys.push(key);
    };
    add(raw);
    const body = raw.indexOf("SZ") === 0 ? raw.slice(2) : raw;
    add(body);
    const stripped = body.replace(/^0+/, "");
    add(stripped);
    if (stripped) add("SZ" + stripped);
    return keys;
  },
  applyCoachGps(rows, vehicles, nowSec) {
    const list = Array.isArray(rows) ? rows : [];
    const vehs = Array.isArray(vehicles) ? vehicles : [];
    const byRoute = /* @__PURE__ */ new Map();
    const fold = (value) => String(value || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, "");
    vehs.forEach((vehicle) => {
      if (!vehicle) return;
      if (!BkkLib.mapGpsLatLon(vehicle.lat, vehicle.lon)) return;
      BkkLib.coachRouteKeys(vehicle.route).forEach((key) => {
        if (!byRoute.has(key)) byRoute.set(key, []);
        byRoute.get(key).push(vehicle);
      });
    });
    const now = Number.isFinite(Number(nowSec)) ? Number(nowSec) : Math.floor(Date.now() / 1e3);
    const rowHead = (row) => fold(row.headsign || row.head);
    const vehicleClassOk = (row, vehicle) => {
      const route = String(vehicle.route || "").trim().toUpperCase();
      const kind = String(row.vehicle || "").toLowerCase();
      if (route.indexOf("SZ") === 0) return kind === "bus";
      if (/^\d{4}$/.test(route)) return kind === "coach";
      return true;
    };
    const pairOk = (row, vehicle) => {
      if (!vehicleClassOk(row, vehicle)) return false;
      const head = rowHead(row);
      const vehicleHead = fold(vehicle.head);
      if (head && vehicleHead && (head.indexOf(vehicleHead) >= 0 || vehicleHead.indexOf(head) >= 0)) return true;
      if (!vehicleHead) {
        const dep = Number(row.dep || row.sched || 0);
        return dep > 0 && Math.abs(dep - now) <= 20 * 60;
      }
      return false;
    };
    const groups = /* @__PURE__ */ new Map();
    list.forEach((row, idx) => {
      if (!row || !/^(gtfs:|motis:)/.test(String(row.tripId || ""))) return;
      if (BkkLib.mapGpsLatLon(row.lat, row.lon) && !row.gpsCarried) return;
      let key = "";
      BkkLib.coachRouteKeys(row.label).some((candidate) => {
        if (!byRoute.has(candidate)) return false;
        key = candidate;
        return true;
      });
      if (!key) return;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(idx);
    });
    const vehicleId = (vehicle) => String(vehicle.tripId || vehicle.lat + "," + vehicle.lon);
    const pairs = [];
    groups.forEach((indexes, key) => {
      const uniq = [];
      const seen = /* @__PURE__ */ new Set();
      byRoute.get(key).forEach((vehicle) => {
        const id = vehicleId(vehicle);
        if (seen.has(id)) return;
        seen.add(id);
        uniq.push(vehicle);
      });
      indexes.forEach((idx) => {
        uniq.forEach((vehicle) => {
          if (!pairOk(list[idx], vehicle)) return;
          const dep = Number(list[idx].dep || list[idx].sched || 0);
          const head = rowHead(list[idx]);
          const vehicleHead = fold(vehicle.head);
          const headMatch = !!(head && vehicleHead && (head.indexOf(vehicleHead) >= 0 || vehicleHead.indexOf(head) >= 0));
          const distance = Math.abs((dep || now) - now);
          pairs.push({ idx, vehicle, score: (headMatch ? 0 : 1) * 1e12 + distance });
        });
      });
    });
    pairs.sort((a, b) => a.score - b.score);
    const usedRow = /* @__PURE__ */ new Set();
    const usedVehicle = /* @__PURE__ */ new Set();
    pairs.forEach((pair) => {
      const vid = vehicleId(pair.vehicle);
      if (usedRow.has(pair.idx) || usedVehicle.has(vid)) return;
      usedRow.add(pair.idx);
      usedVehicle.add(vid);
      const gps = BkkLib.mapGpsLatLon(pair.vehicle.lat, pair.vehicle.lon);
      if (!gps) return;
      list[pair.idx].lat = gps[0];
      list[pair.idx].lon = gps[1];
      delete list[pair.idx].gpsCarried;
    });
    list.forEach((row) => {
      if (!row || !row.gpsCarried) return;
      row.lat = null;
      row.lon = null;
      delete row.gpsCarried;
    });
    return list;
  },
  async attachCoachGps(hass, rows) {
    const list = Array.isArray(rows) ? rows : [];
    const needs = list.some((row) => row && /^(gtfs:|motis:)/.test(String(row.tripId || "")) && (row.gpsCarried || !BkkLib.mapGpsLatLon(row.lat, row.lon)));
    if (!hass || !needs) return list;
    return BkkLib.applyCoachGps(list, await BkkLib.coachPositions(hass));
  },
  keepFutarCandidate(rt, selected, mode, destKey) {
    if (!selected || !selected.size || mode === "volan") return true;
    if (destKey && (mode === "bkk" || mode === "mav" || mode === "all")) return true;
    const rid = rt && rt.id || "";
    return selected.has(rid);
  },
  genericRailLabel(label) {
    return /^(IC|EC|IR|EX|EN|RJX|S|SZ)$/i.test(String(label || "").trim());
  },
  suburbanLineLabel(label) {
    return /^[SGZ]\d/i.test(String(label || "").trim());
  },
  /* True when the row's visible jelzés is a MÁV train number (16907) or
     generic EX/EN plus a long number. These stay on the hop; merge them
     onto a branded suburban/named row when they are the same departure. */
  numberedRailBadge(row) {
    if (!row || !BkkLib.isTrainRow(row)) return false;
    const label = String(row.label || "").trim();
    if (/^\d+$/.test(label)) return true;
    if (/^(EX|EN)$/i.test(label) && /^\d{4,}$/.test(BkkLib.rowTrainNumber(row))) return true;
    return false;
  },
  platformFromText(text) {
    const m = String(text || "").match(PLATFORM_IN_HEAD);
    return m ? m[1] : "";
  },
  stripPlatform(text) {
    return String(text || "").replace(PLATFORM_STRIP, "").replace(/\s+/g, " ").trim();
  },
  /* Home Assistant hands over a new states object on every change and the
     same one in between, so one scan of all states serves every paint of
     every card until something changes. The list is shared: read only. */
  _hassVehicles: /* @__PURE__ */ new WeakMap(),
  collectHassVehicles(hass) {
    const states = hass && hass.states;
    if (!states || typeof states !== "object") return [];
    const hit = BkkLib._hassVehicles.get(states);
    if (hit) return hit.all;
    const out = [];
    Object.keys(states).forEach((eid) => {
      if (eid.indexOf("sensor.") !== 0) return;
      const vehs = ((states[eid] || {}).attributes || {}).vehicles;
      if (!Array.isArray(vehs)) return;
      vehs.forEach((v) => {
        if (v) out.push(v);
      });
    });
    BkkLib._hassVehicles.set(states, { all: out, emma: null });
    return out;
  },
  /* The EMMA (non-FUTÁR) vehicles of the same scan, keyed for matching. */
  _hassEmmaKeyed(hass, pool) {
    const memo = hass && hass.states && BkkLib._hassVehicles.get(hass.states);
    if (memo && memo.all === pool && memo.emma) return memo.emma;
    const keyed = BkkLib._emmaKeyed(pool.filter((v) => !BkkLib.isFutarTripId(v && v.tripId)).map((v) => ({
      lat: v.lat,
      lon: v.lon,
      heading: v.heading,
      speed: v.speed,
      label: v.label || v.trainNumber,
      trip: {
        tripShortName: String(
          v.trip && (v.trip.tripShortName || v.trip.shortName) || v.trainNumber || v.shortName || v.label || ""
        )
      }
    })));
    if (memo && memo.all === pool) memo.emma = keyed;
    return keyed;
  },
  matchHassVehicle(pool, row) {
    const trip = String(row.tripId || "");
    const num = String(row.trainNumber || BkkLib.trainNumberFromTripId(row.tripId) || "");
    const hm = BkkLib.hm(row.sched || row.dep);
    const label = String(row.label || "").toUpperCase();
    let best = null;
    let bestScore = 0;
    pool.forEach((v) => {
      let s = 0;
      if (trip && v.tripId && String(v.tripId) === trip) s += 5;
      if (num && v.trainNumber && String(v.trainNumber) === num) s += 3;
      if (hm && v.attime && String(v.attime) === hm) s += 2;
      const vr = String(v.routeid || "").toUpperCase();
      if (label && vr && label === vr) s += 1;
      if (s > bestScore) {
        bestScore = s;
        best = v;
      }
    });
    if (!best) return null;
    if (bestScore >= 5) return best;
    if (num && bestScore >= 3) return best;
    if (hm && label && bestScore >= 3) return best;
    return null;
  },
  /* BKK has no live track or IC seat-reservation flag. Those live on the
     bkk_stop sensors (ELVIRA). Copy them onto the same departure. */
  enrichFromHass(hass, rows) {
    const pool = BkkLib.collectHassVehicles(hass);
    if (!pool.length || !rows || !rows.length) return rows;
    rows.forEach((row) => {
      const v = BkkLib.matchHassVehicle(pool, row);
      if (!v) return;
      if (!row.platform && v.platform) row.platform = String(v.platform);
      if (!row.platform) {
        const fromHead = BkkLib.platformFromText(v.headsign);
        if (fromHead) row.platform = fromHead;
      }
      if (v.booking) row.booking = true;
      if (!row.trainNumber && v.trainNumber) row.trainNumber = String(v.trainNumber);
      if (v.tripId && BkkLib.isFutarTripId(v.tripId) && !BkkLib.isFutarTripId(row.tripId)) {
        row.tripId = String(v.tripId);
      }
      if (v.bikesAllowed === true || v.bikesallowed === true) row.bikesAllowed = true;
      const incoming = BkkLib.mapGpsLatLon(v.lat, v.lon);
      if (!BkkLib.mapGpsLatLon(row.lat, row.lon) && incoming) {
        row.lat = incoming[0];
        row.lon = incoming[1];
      }
    });
    BkkLib._applyEmmaKeyed(rows, BkkLib._hassEmmaKeyed(hass, pool));
    return rows;
  },
  applyRailBadge(row) {
    if (!row) return row;
    let key = String(row.label || "").trim().toUpperCase().normalize("NFC");
    if (key === "S") {
      row.label = "Sz";
      key = "SZ";
    }
    let badge = BKK_RAIL_BADGE[key];
    if (!badge && BkkLib.isTrainRow(row) && key && !BkkLib.suburbanLineLabel(key) && !BkkLib.genericRailLabel(key) && !/^\d+$/.test(key)) {
      badge = BKK_RAIL_BADGE.IC;
    }
    if (!badge) return row;
    const cur = String(row.color || "").replace("#", "").toLowerCase();
    if (!cur || cur === "4477aa") {
      row.color = "#" + badge[0];
      row.text = "#" + badge[1];
    }
    return row;
  },
  /* One departure in the shape the card renders: display strings plus the raw
     epoch seconds, so the countdown can tick without refetching. */
  asRow(r, destName, lang) {
    BkkLib.applyRailBadge(r);
    const dep = r.dep;
    let type = String(r.rawType || r.vehicle || "BUS").toUpperCase();
    if (type.indexOf("SUBURBAN") >= 0 || type === "TRAIN" || type === "RAILWAY") type = "RAIL";
    let head = String(r.head || destName || "").trim();
    const plat = r.platform ? String(r.platform) : BkkLib.platformFromText(head);
    head = BkkLib.stripPlatform(head);
    const lat = Number(r.lat);
    const lon = Number(r.lon);
    const gps = BkkLib.mapGpsLatLon(lat, lon);
    const hasGps = !!gps;
    const shape = Array.isArray(r.shape) ? r.shape : void 0;
    return {
      type,
      icon: vehicleIcon(type),
      label: r.label,
      headsign: head,
      tripId: r.tripId || "",
      depTs: dep || 0,
      schedTs: r.sched || dep || 0,
      attime: BkkLib.hm(r.sched || dep),
      predicted_attime: BkkLib.hm(dep),
      wheelchair: !!r.wheelchair,
      bikesAllowed: !!r.bikesAllowed,
      color: String(r.color || "").replace("#", ""),
      textcolor: String(r.text || r.textcolor || "").replace("#", ""),
      platform: plat || "",
      trainNumber: r.trainNumber || "",
      delay: Number(r.delay || 0),
      booking: !!r.booking,
      nationalPass: !!r.nationalPass,
      reservationFreeSection: !!r.reservationFreeSection,
      travelMin: r.travel,
      lat: hasGps ? gps[0] : null,
      lon: hasGps ? gps[1] : null,
      hasGps,
      shape,
      gtfs: r.gtfs || null,
      vehicle: r.vehicle || "",
      vehicleId: r.vehicleId || "",
      licensePlate: r.licensePlate || "",
      model: r.model || "",
      vehicleStatus: r.vehicleStatus || "",
      stopDistancePercent: r.stopDistancePercent
    };
  }
});

// src/lib/stop-history.js
var HISTORY_KEY = "hungarian_transport_planner_stops";
var HISTORY_KEEP = 24;
var HISTORY_HALF_LIFE_DAYS = 30;
var localKey = (hass) => {
  const uid = hass && hass.user && hass.user.id;
  return uid ? `${HISTORY_KEY}:${uid}` : "";
};
var localRead = (hass) => {
  const key = localKey(hass);
  if (!key) return [];
  try {
    return JSON.parse(globalThis.localStorage.getItem(key) || "[]");
  } catch (_e) {
    return [];
  }
};
var localWrite = (hass, list) => {
  const key = localKey(hass);
  if (!key) return;
  try {
    globalThis.localStorage.setItem(key, JSON.stringify(list));
    globalThis.localStorage.removeItem(HISTORY_KEY);
  } catch (_e) {
  }
};
Object.assign(BkkLib, {
  STOP_HISTORY_KEY: HISTORY_KEY,
  stopHistoryLocalKey: localKey,
  _stopHistory: null,
  /* Writes queue up behind each other: two picks in one tick (an origin and
     a destination) must both land in the list. */
  _stopHistoryWrite: Promise.resolve(),
  stopHistoryScore(entry, now) {
    const ageDays = Math.max(0, ((now || Date.now()) - (Number(entry && entry.t) || 0)) / 864e5);
    return (Number(entry && entry.n) || 1) * Math.pow(0.5, ageDays / HISTORY_HALF_LIFE_DAYS);
  },
  _cleanStopHistory(list) {
    const out = [];
    const seen = /* @__PURE__ */ new Set();
    (Array.isArray(list) ? list : []).forEach((e) => {
      if (!e || !e.id || !e.name) return;
      const id = String(e.id);
      if (seen.has(id)) return;
      seen.add(id);
      const row = { id, name: String(e.name), n: Math.max(1, Number(e.n) || 1), t: Number(e.t) || 0 };
      if (Number.isFinite(Number(e.lat)) && Number.isFinite(Number(e.lon)) && e.lat !== null && e.lon !== null) {
        row.lat = Number(e.lat);
        row.lon = Number(e.lon);
      }
      if (e.kind) row.kind = String(e.kind);
      if (e.label) row.label = String(e.label);
      out.push(row);
    });
    return out;
  },
  /* The server's list for this user; null when it cannot be read (no
     websocket, or the call failed), which is not the same as empty. */
  async _readServerHistory(hass) {
    if (!hass || typeof hass.callWS !== "function") return null;
    try {
      const res = await hass.callWS({ type: "frontend/get_user_data", key: HISTORY_KEY });
      return Array.isArray(res && res.value) ? res.value : [];
    } catch (_e) {
      return null;
    }
  },
  /* Two copies of one history (the server's and this browser's, or two
     devices'): every stop of either, each with its higher count and its
     later use. */
  _mergeStopHistory(a, b) {
    const byId = /* @__PURE__ */ new Map();
    BkkLib._cleanStopHistory(a).concat(BkkLib._cleanStopHistory(b)).forEach((e) => {
      const prev = byId.get(e.id);
      if (!prev) {
        byId.set(e.id, e);
        return;
      }
      const newer = e.t > prev.t ? e : prev;
      byId.set(e.id, Object.assign({}, prev, newer, { n: Math.max(prev.n, e.n), t: Math.max(prev.t, e.t) }));
    });
    return Array.from(byId.values());
  },
  /* The most used stops first, at most HISTORY_KEEP of them. */
  _rankStopHistory(list, now) {
    return BkkLib._cleanStopHistory(list.slice().sort((a, b) => BkkLib.stopHistoryScore(b, now) - BkkLib.stopHistoryScore(a, now)).slice(0, HISTORY_KEEP));
  },
  /* Whether `local` has a stop the server's list lacks, or a later or more
     counted use of one: picks made while the server could not be read. */
  _stopHistoryAhead(local, server) {
    const byId = new Map(server.map((e) => [e.id, e]));
    return local.some((e) => {
      const s = byId.get(e.id);
      return !s || e.n > s.n || e.t > s.t;
    });
  },
  /* Picks kept only in this browser go to the server: read again right
     before writing (behind any write in flight), so nothing newer is lost. */
  _pushLocalStopHistory(hass) {
    const run = BkkLib._stopHistoryWrite.then(async () => {
      const server = await BkkLib._readServerHistory(hass);
      if (server === null) return;
      const cleanServer = BkkLib._cleanStopHistory(server);
      const local = BkkLib._cleanStopHistory(localRead(hass));
      if (!BkkLib._stopHistoryAhead(local, cleanServer)) return;
      const next = BkkLib._rankStopHistory(BkkLib._mergeStopHistory(cleanServer, local), Date.now());
      localWrite(hass, next);
      await hass.callWS({ type: "frontend/set_user_data", key: HISTORY_KEY, value: next });
    });
    BkkLib._stopHistoryWrite = run.catch(() => {
    });
    return BkkLib._stopHistoryWrite;
  },
  /* One read per connection; a new login reads again. A failed read shows
     the browser's copy but is not kept: the next look asks again. A good
     read shows the server's list with this browser's picks merged in, and
     sends those picks up when the server lacks them. */
  loadStopHistory(hass) {
    const conn = hass && hass.connection || null;
    const memo = BkkLib._stopHistory;
    if (memo && memo.conn === conn) return memo.p;
    const p = (async () => {
      const server = await BkkLib._readServerHistory(hass);
      const local = BkkLib._cleanStopHistory(localRead(hass));
      if (server === null) {
        if (BkkLib._stopHistory && BkkLib._stopHistory.p === p) BkkLib._stopHistory = null;
        return local;
      }
      const cleanServer = BkkLib._cleanStopHistory(server);
      if (!BkkLib._stopHistoryAhead(local, cleanServer)) return cleanServer;
      BkkLib._pushLocalStopHistory(hass);
      return BkkLib._rankStopHistory(BkkLib._mergeStopHistory(cleanServer, local), Date.now());
    })();
    BkkLib._stopHistory = { conn, p };
    return p;
  },
  /* Whether this connection's list is held (read from the server, or
     being read): false after a failed read, so the caller asks again. */
  stopHistoryKept(hass) {
    const memo = BkkLib._stopHistory;
    return !!memo && memo.conn === (hass && hass.connection || null);
  },
  /* Count a pick (an origin, a destination or a suggestion), keep the most
     used, and store the list for this user. */
  recordStop(hass, stop) {
    if (!stop || !stop.id || !stop.name) return BkkLib.loadStopHistory(hass);
    const run = BkkLib._stopHistoryWrite.then(() => BkkLib._recordStopNow(hass, stop));
    BkkLib._stopHistoryWrite = run.catch(() => {
    });
    return run;
  },
  /* The server's list is read again right before each write, so another
     device's picks since this page loaded are kept, not overwritten. When
     it cannot be read, only this browser's copy is written: a list that
     may be stale must not replace the server's. */
  async _recordStopNow(hass, stop) {
    const server = await BkkLib._readServerHistory(hass);
    const memo = BkkLib._stopHistory;
    const conn = hass && hass.connection || null;
    const seen = memo && memo.conn === conn ? await memo.p.catch(() => []) : [];
    const list = BkkLib._mergeStopHistory(server || [], BkkLib._mergeStopHistory(seen, localRead(hass)));
    const now = Date.now();
    const id = String(stop.id);
    let entry = list.find((e) => e.id === id);
    if (!entry) {
      entry = { id, name: String(stop.name), n: 0, t: 0 };
      list.push(entry);
    }
    entry.n += 1;
    entry.t = now;
    entry.name = String(stop.name);
    if (Number.isFinite(Number(stop.lat)) && Number.isFinite(Number(stop.lon)) && stop.lat !== null && stop.lon !== null) {
      entry.lat = Number(stop.lat);
      entry.lon = Number(stop.lon);
    }
    if (stop.kind) entry.kind = String(stop.kind);
    if (stop.label) entry.label = String(stop.label);
    const next = BkkLib._rankStopHistory(list, now);
    BkkLib._stopHistory = server !== null ? { conn, p: Promise.resolve(next) } : null;
    localWrite(hass, next);
    if (server !== null) {
      try {
        await hass.callWS({ type: "frontend/set_user_data", key: HISTORY_KEY, value: next });
      } catch (_e) {
      }
    }
    return next;
  },
  /* The most used stops first, without the one already picked. */
  frequentStops(list, opts) {
    opts = opts || {};
    const now = opts.now || Date.now();
    const skip = String(opts.excludeId || "");
    return (list || []).filter((e) => e && e.id && e.id !== skip).slice().sort((a, b) => BkkLib.stopHistoryScore(b, now) - BkkLib.stopHistoryScore(a, now)).slice(0, opts.limit || 6);
  }
});

// src/editor.js
var BKKHopCardEditor = class extends HTMLElement {
  constructor() {
    super();
    this._config = { routeIds: [] };
    this._built = false;
    this._searchTimer = null;
    this._destTimer = null;
    this._loadingKey = false;
    this._cache = {};
    this._dests = [];
    this._destRoutes = {};
    this._loadedStop = "";
    this._loadedAccess = "";
    this._loadingStop = "";
    this._destsLoading = false;
    this._keyOk = null;
    this._probingKey = false;
    this._probeRetryMs = 3e4;
    this._probeRetryAt = 0;
    this._fillKeyP = null;
    this._keyDirty = false;
    this._userEditedKey = false;
    this._keyReveal = false;
    this._keyVisible = false;
    this._searchSeq = 0;
    this._destSeq = 0;
    this._citySeq = 0;
    this._autoTitle = "";
  }
  disconnectedCallback() {
    this._cancelSearches();
  }
  /* A pick, a reset or a mode change ends the searches still typed or in
     flight: their hits belong to the old field and must not paint under it. */
  _cancelSearches() {
    if (this._searchTimer) {
      clearTimeout(this._searchTimer);
      this._searchTimer = null;
    }
    if (this._destTimer) {
      clearTimeout(this._destTimer);
      this._destTimer = null;
    }
    this._searchSeq += 1;
  }
  setConfig(config) {
    this._config = normalizeCardConfig(config);
    BkkLib.warnCardApiKey(this._config);
    if (!this._cache) this._cache = {};
    if (!this._dests) this._dests = [];
    if (!this._destRoutes) this._destRoutes = {};
    if (!this._built) this._build();
    this._syncLang();
    this._syncTexts();
    this._syncName();
    this._syncStopLabel();
    this._syncDestLabel();
    this._syncMinutesAfter();
    this._syncApiKey();
    this._syncMode();
    this._probeKey();
    const stopId = this._config.stopId;
    const loading = this._destsLoading && this._loadingStop === stopId;
    if (stopId && this._canLoadDests() && this._destsStale(stopId) && !loading) {
      this._loadDests();
    }
  }
  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    BkkLib._hass = hass;
    if (first && this._built && this._lang() !== resolveLang(this._config.language, null)) {
      this._syncTexts();
      this._syncMode();
    }
    const viaIntegration = BkkLib.integrationHasKey(hass);
    if (!this._userEditedKey && !this._config.apiKey && !this._borrowedKey && !this._loadingKey && !viaIntegration && !this._keyless()) this._fillKey();
    else if (this._hasFutar()) this._probeKey();
    const stopId = this._config.stopId;
    if (stopId && this._loadedStop === stopId && this._destsStale(stopId) && !(this._destsLoading && this._loadingStop === stopId)) this._loadDests();
  }
  _apiKey() {
    return BkkLib.cardApiKey(this._config, this._hass, this._borrowedKey);
  }
  _hasFutar() {
    return BkkLib.hasFutar(this._apiKey(), this._hass);
  }
  /* What a probe result belongs to: this card's key, or the integration. */
  _keyAccess() {
    const key = this._apiKey();
    if (key) return `key:${key}`;
    return BkkLib.integrationHasKey(this._hass) ? "integration" : "";
  }
  _lang() {
    return resolveLang(this._config.language, this._hass);
  }
  _mode() {
    return BkkLib.mode(this._config);
  }
  /* Volán and city boards come from the GTFS indexes: no FUTÁR key, and
     none copied in from a sibling card (the card itself never asks). */
  _keyless() {
    const mode = this._mode();
    return mode === "volan" || mode === "helyi";
  }
  /* Only the BKK and MÁV modes need FUTÁR: "all" falls back to the GTFS
     indexes without a key, as the card itself does. */
  _needsKey() {
    const mode = this._mode();
    return mode === "bkk" || mode === "mav";
  }
  _canLoadDests() {
    return this._hasFutar() || !this._needsKey();
  }
  _destsStale(stopId) {
    if (this._loadedStop !== stopId) return true;
    return !this._loadedAccess && !this._keyless() && !!this._keyAccess();
  }
  _build() {
    this._built = true;
    this.innerHTML = `
      <style>
        .card-config { padding: 8px 0 4px; }
        .card-config .f { margin-bottom: 18px; }
        .card-config label {
          display: block; font-weight: 500; margin-bottom: 8px;
          color: var(--primary-text-color);
        }
        .card-config input, .card-config select {
          width: 100%; box-sizing: border-box; font: inherit;
          background: var(--mdc-text-field-fill-color, var(--secondary-background-color, rgba(128,128,128,.12)));
          color: var(--primary-text-color);
          border: 0; border-bottom: 1px solid var(--divider-color);
          border-radius: 4px 4px 0 0; padding: 12px 12px 10px;
        }
        .card-config .hint { font-size: 12px; color: var(--secondary-text-color); margin: 6px 0 0; }
        .card-config .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
        .card-config .chip.on {
          color: var(--text-primary-color, #fff);
          background: var(--primary-color);
          border-color: var(--primary-color);
        }
        .card-config .hit {
          display: block; width: 100%; text-align: left; font: inherit;
          background: transparent; color: var(--primary-text-color);
          border: 0; border-bottom: 1px solid var(--divider-color);
          padding: 10px 8px; cursor: pointer;
        }
        .card-config .hit:hover { background: var(--secondary-background-color); }
        .card-config .list { max-height: 200px; overflow: auto; }
        .card-config .picked { font-weight: 600; margin-top: 6px; }
        .card-config .err { color: var(--error-color, #e66); font-size: 12px; margin-top: 8px; }
        .card-config .switch {
          display: flex; align-items: center; gap: 10px; font-weight: 500;
        }
        .card-config .switch input { width: auto; margin: 0; }
        .card-config .row { display: flex; gap: 18px; flex-wrap: wrap; }
        .card-config .row .switch { margin-right: 8px; }
        .card-config .keyrow { display: flex; gap: 6px; align-items: stretch; }
        .card-config .keyrow input { flex: 1 1 auto; min-width: 0; }
        .card-config .reveal {
          flex: 0 0 auto; font: inherit; cursor: pointer; padding: 0 12px;
          background: transparent; color: var(--primary-color);
          border: 1px solid var(--divider-color); border-radius: 4px;
        }
        .card-config a { color: var(--primary-color); }
      </style>
      <div class="card-config">
        <div class="f">
          <label id="lblLang" for="lang"></label>
          <select id="lang">
            <option value="auto"></option>
            <option value="hu"></option>
            <option value="en"></option>
          </select>
        </div>
        <div class="f" id="apiKeyWrap">
          <label id="lblApiKey" for="apiKey"></label>
          <div class="keyrow">
            <input id="apiKey" type="password" autocomplete="off" spellcheck="false">
            <button type="button" class="reveal" id="apiKeyShow" aria-pressed="false"></button>
          </div>
          <div class="hint"><span id="hintApiKey"></span>
            <a href="https://opendata.bkk.hu/data-sources" target="_blank" rel="noopener">opendata.bkk.hu/data-sources</a>
          </div>
        </div>
        <div class="f" id="apiKeyOkWrap" style="display:none">
          <div class="hint"><span id="apiKeyOkText"></span>
            <a href="#" id="apiKeyEdit"></a>
          </div>
        </div>
        <div class="f" id="apiKeyDeprecatedWrap" style="display:none">
          <div class="hint" id="apiKeyDeprecated"></div>
        </div>
        <div class="f">
          <label id="lblName" for="name"></label>
          <input id="name" type="text" placeholder="Hungarian transport">
        </div>
        <div class="f">
          <div class="row" id="modeSwitches">
            <label class="switch"><input id="mav" type="checkbox"> MÁV</label>
            <label class="switch"><input id="volan" type="checkbox"> Volán</label>
            <label class="switch"><input id="helyi" type="checkbox"> <span id="lblHelyi"></span></label>
          </div>
          <div class="hint" id="modeHint"></div>
        </div>
        <div class="f" id="cityWrap" style="display:none">
          <label id="lblCity" for="city"></label>
          <select id="city"></select>
        </div>
        <div class="f">
          <label id="lblStop" for="q"></label>
          <input id="q" type="search">
          <div class="chips" id="fav"></div>
          <div class="picked" id="stopPicked"></div>
          <div class="list" id="hits"></div>
        </div>
        <div class="f">
          <label id="lblDest" for="dq"></label>
          <input id="dq" type="search">
          <div class="picked" id="destPicked"></div>
          <div class="list" id="destHits"></div>
        </div>
        <div class="f">
          <label id="lblMinutesAfter" for="minutesAfter"></label>
          <select id="minutesAfter"></select>
          <div class="chips" id="minutesAfterChips"></div>
          <div class="hint" id="hintMinutesAfter"></div>
        </div>
        <div class="err" id="err"></div>
      </div>
    `;
    this._el("lang").addEventListener("change", (ev) => {
      this._emit({ language: ev.target.value });
      this._syncTexts();
      this._syncMode();
      this._paintDestHits(this._el("dq") ? this._el("dq").value.trim() : "");
    });
    this._el("name").addEventListener("input", (ev) => {
      this._emit({ name: ev.target.value });
    });
    this._el("apiKey").addEventListener("input", () => {
      this._keyDirty = true;
      this._userEditedKey = true;
    });
    this._el("apiKey").addEventListener("change", (ev) => {
      this._keyDirty = false;
      this._userEditedKey = true;
      this._keyOk = null;
      this._probedKey = "";
      this._error("");
      this._emit({ apiKey: ev.target.value.trim() });
      this._probeKey();
    });
    this._el("apiKeyShow").addEventListener("click", () => {
      this._keyVisible = !this._keyVisible;
      this._syncKeyReveal();
    });
    this._el("apiKeyEdit").addEventListener("click", (ev) => {
      ev.preventDefault();
      this._keyReveal = true;
      this._syncApiKey();
      const el = this._el("apiKey");
      if (el) el.focus();
    });
    this._el("mav").addEventListener("change", () => this._onModeToggle("mav"));
    this._el("volan").addEventListener("change", () => this._onModeToggle("volan"));
    this._el("helyi").addEventListener("change", () => this._onModeToggle("helyi"));
    this._el("city").addEventListener("change", (ev) => {
      this._cancelSearches();
      this._keepAutoTitle();
      this._emit(Object.assign({ city: ev.target.value }, this._noStops()));
      this._dests = [];
      this._loadedStop = "";
      this._syncStopLabel();
      this._syncDestLabel();
    });
    this._el("q").addEventListener("input", () => {
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => this._search(this._el("q").value.trim(), "origin"), 280);
    });
    this._el("dq").addEventListener("input", () => {
      clearTimeout(this._destTimer);
      this._destTimer = setTimeout(() => this._search(this._el("dq").value.trim(), "dest"), 280);
    });
    this._el("dq").addEventListener("focus", () => {
      this._search(this._el("dq").value.trim(), "dest");
    });
    this._el("minutesAfter").addEventListener("change", (ev) => {
      const next = clampMinutesAfter(ev.target.value);
      if (next === clampMinutesAfter(this._config.minutesAfter)) {
        this._syncMinutesAfter();
        return;
      }
      this._emit({ minutesAfter: next });
    });
    this._paintFav();
  }
  _el(id) {
    return this.querySelector("#" + id);
  }
  /* The editor lives in HA's shadow tree. document.activeElement is the host,
     so walking only shadowRoots never reaches these light-DOM inputs. */
  _focusedEl() {
    try {
      return this.querySelector(":focus");
    } catch (_e) {
      return null;
    }
  }
  _isEditing(el) {
    if (!el) return false;
    const focused = this._focusedEl();
    if (focused === el) return true;
    if (focused && el.contains && el.contains(focused)) return true;
    try {
      if (typeof el.matches === "function" && el.matches(":focus, :focus-within")) return true;
    } catch (_e) {
    }
    return false;
  }
  _syncName() {
    const el = this._el("name");
    if (!el || this._isEditing(el)) return;
    el.value = this._config.name || "";
  }
  _syncStopLabel() {
    const el = this._el("stopPicked");
    if (el) el.textContent = this._config.stopName || "";
    const q = this._el("q");
    if (q && !this._isEditing(q) && this._config.stopName) {
      q.placeholder = this._config.stopName;
    }
  }
  _syncDestLabel() {
    const el = this._el("destPicked");
    if (el) el.textContent = this._config.destName || "";
    const q = this._el("dq");
    if (q && !this._isEditing(q) && this._config.destName) {
      q.placeholder = this._config.destName;
    }
  }
  _syncMinutesAfter() {
    const cur = clampMinutesAfter(this._config.minutesAfter);
    const sel = this._el("minutesAfter");
    if (sel) {
      const lang = this._lang();
      const values = MINUTES_AFTER_PRESETS.slice();
      if (values.indexOf(cur) < 0) values.push(cur);
      values.sort((a, b) => a - b);
      const sig = lang + ":" + values.join(",");
      if (sel.dataset.opts !== sig) {
        sel.innerHTML = values.map((n) => `<option value="${n}">${BkkLib.esc(t(lang, "minutes", n))}</option>`).join("");
        sel.dataset.opts = sig;
      }
      if (!this._isEditing(sel)) sel.value = String(cur);
    }
    const box = this._el("minutesAfterChips");
    if (!box) return;
    const signature = String(cur);
    if (box.dataset.cur === signature && box.childElementCount) {
      Array.from(box.children).forEach((b) => {
        b.classList.toggle("on", Number(b.getAttribute("data-min")) === cur);
      });
      return;
    }
    box.innerHTML = "";
    box.dataset.cur = signature;
    MINUTES_AFTER_PRESETS.forEach((n) => {
      const b = document.createElement("button");
      b.className = "chip" + (n === cur ? " on" : "");
      b.type = "button";
      b.setAttribute("data-min", String(n));
      b.textContent = String(n);
      b.addEventListener("click", () => {
        if (n === clampMinutesAfter(this._config.minutesAfter)) return;
        this._emit({ minutesAfter: n });
      });
      box.appendChild(b);
    });
  }
  _syncApiKey() {
    this._syncApiKeyVisibility();
    const wrap = this._el("apiKeyWrap");
    const hidden = wrap && wrap.style.display === "none";
    const el = this._el("apiKey");
    if (!el || this._isEditing(el) || this._keyDirty) return;
    el.value = hidden ? "" : this._config.apiKey || "";
  }
  /* A working key is noise in the form, but it still has to be replaceable
     without hand-editing YAML, hence the "change" link. */
  _syncApiKeyVisibility() {
    const wrap = this._el("apiKeyWrap");
    const okWrap = this._el("apiKeyOkWrap");
    if (!wrap) return;
    const depWrap = this._el("apiKeyDeprecatedWrap");
    const dep = this._el("apiKeyDeprecated");
    const showDeprecated = (on) => {
      if (depWrap) depWrap.style.display = on ? "" : "none";
      if (dep) dep.textContent = on ? t(this._lang(), "apiKeyDeprecated") : "";
    };
    const own = (this._config.apiKey || "").trim();
    const mode = this._mode();
    if (mode === "volan" || mode === "helyi") {
      wrap.style.display = "none";
      if (okWrap) okWrap.style.display = "none";
      showDeprecated(!!own);
      return;
    }
    const viaIntegration = !own && BkkLib.integrationHasKey(this._hass);
    const borrowed = !own && !viaIntegration && !!String(this._borrowedKey || "").trim();
    const settled = !this._keyReveal && (viaIntegration && this._keyOk !== false || (!!own || borrowed) && this._keyOk === true);
    wrap.style.display = settled ? "none" : "";
    if (okWrap) okWrap.style.display = settled ? "" : "none";
    const okText = this._el("apiKeyOkText");
    if (okText) {
      const okKey = viaIntegration ? "apiKeyIntegration" : borrowed ? "apiKeyBorrowed" : "apiKeyOk";
      okText.textContent = t(this._lang(), okKey);
    }
    showDeprecated(!!own);
  }
  /* The key is a password field; the button shows it to check a paste. */
  _syncKeyReveal() {
    const el = this._el("apiKey");
    const btn = this._el("apiKeyShow");
    if (el) el.type = this._keyVisible ? "text" : "password";
    if (!btn) return;
    const label = t(this._lang(), this._keyVisible ? "apiKeyHide" : "apiKeyShow");
    btn.textContent = label;
    btn.setAttribute("aria-pressed", this._keyVisible ? "true" : "false");
  }
  _modeCopy(mode) {
    return modeCopy(this._lang(), mode);
  }
  _syncLang() {
    const el = this._el("lang");
    if (!el) return;
    const lang = this._lang();
    el.options[0].textContent = t(lang, "langAuto");
    el.options[1].textContent = t(lang, "langHu");
    el.options[2].textContent = t(lang, "langEn");
    const pref = this._config.language === "hu" || this._config.language === "en" ? this._config.language : "auto";
    if (!this._isEditing(el)) el.value = pref;
  }
  _syncTexts() {
    const lang = this._lang();
    this._syncLang();
    const set = (id, text) => {
      const el = this._el(id);
      if (el) el.textContent = text;
    };
    set("lblLang", t(lang, "langLabel"));
    set("lblApiKey", t(lang, "apiKeyLabel"));
    set("hintApiKey", t(lang, "apiKeyHint"));
    set("apiKeyOkText", t(lang, "apiKeyOk"));
    set("apiKeyEdit", t(lang, "apiKeyChange"));
    set("lblName", t(lang, "nameLabel"));
    set("lblHelyi", t(lang, "helyiLabel"));
    set("lblCity", t(lang, "cityLabel"));
    set("lblMinutesAfter", t(lang, "minutesAfterLabel"));
    set("hintMinutesAfter", t(lang, "minutesAfterHint", [MIN_MINUTES_AFTER, MAX_MINUTES_AFTER, DEFAULT_MINUTES_AFTER]));
    this._syncMinutesAfter();
    const key = this._el("apiKey");
    if (key) key.placeholder = t(lang, "apiKeyPlaceholder");
    this._syncKeyReveal();
    const name = this._el("name");
    if (name) name.placeholder = t(lang, "namePlaceholder");
    this._syncErr();
  }
  _syncMode() {
    const mode = this._mode();
    const mav = this._el("mav");
    const volan = this._el("volan");
    const helyi = this._el("helyi");
    if (mav && !this._isEditing(mav)) mav.checked = mode === "mav";
    if (volan && !this._isEditing(volan)) volan.checked = mode === "volan";
    if (helyi && !this._isEditing(helyi)) helyi.checked = mode === "helyi";
    const wrap = this._el("cityWrap");
    if (wrap) wrap.style.display = mode === "helyi" ? "" : "none";
    if (mode === "helyi") this._fillCities();
    const copy = this._modeCopy(mode);
    const hint = this._el("modeHint");
    if (hint) hint.textContent = copy.hint;
    const lblStop = this._el("lblStop");
    if (lblStop) lblStop.textContent = copy.stop;
    const lblDest = this._el("lblDest");
    if (lblDest) lblDest.textContent = copy.dest;
    const q = this._el("q");
    if (q && !this._isEditing(q) && !this._config.stopName) q.placeholder = copy.originPh;
    const dq = this._el("dq");
    if (dq && !this._isEditing(dq) && !this._config.destName) dq.placeholder = copy.destPh;
    this._syncApiKeyVisibility();
    this._paintFav();
  }
  _paintFav() {
    const fav = this._el("fav");
    if (!fav) return;
    fav.innerHTML = "";
    const mode = this._mode();
    const custom = Array.isArray(this._config.favorites) ? this._config.favorites : null;
    const list = (custom || DEFAULT_FAVORITES[mode] || []).filter((f) => f && f.id && f.name);
    const shown = !custom && mode === "all" ? BkkLib.collapseSameNameStops(list) : list;
    shown.forEach((f) => {
      const b = document.createElement("button");
      b.className = "chip";
      b.type = "button";
      b.textContent = String(f.name).split(" / ")[0];
      b.addEventListener("click", () => this._pickStop(f, "origin"));
      fav.appendChild(b);
    });
  }
  _onModeToggle(which) {
    const mavOn = !!(this._el("mav") && this._el("mav").checked);
    const volanOn = !!(this._el("volan") && this._el("volan").checked);
    const helyiOn = !!(this._el("helyi") && this._el("helyi").checked);
    let mav = mavOn;
    let volan = volanOn;
    let helyi = helyiOn;
    if (which === "mav" && mav) {
      volan = false;
      helyi = false;
    }
    if (which === "volan" && volan) {
      mav = false;
      helyi = false;
    }
    if (which === "helyi" && helyi) {
      mav = false;
      volan = false;
    }
    if (this._el("mav")) this._el("mav").checked = mav;
    if (this._el("volan")) this._el("volan").checked = volan;
    if (this._el("helyi")) this._el("helyi").checked = helyi;
    const q = this._el("q");
    const dq = this._el("dq");
    if (q) q.value = "";
    if (dq) dq.value = "";
    const hits = this._el("hits");
    const destHits = this._el("destHits");
    if (hits) hits.innerHTML = "";
    if (destHits) destHits.innerHTML = "";
    this._cancelSearches();
    this._dests = [];
    this._destRoutes = {};
    this._cache = {};
    this._loadedStop = "";
    this._keepAutoTitle();
    this._emit(Object.assign({
      mav,
      volan,
      helyi,
      city: helyi ? this._config.city || "" : ""
    }, this._noStops()));
    this._syncMode();
    this._syncStopLabel();
    this._syncDestLabel();
  }
  async _fillCities() {
    const sel = this._el("city");
    if (!sel || this._isEditing(sel)) return;
    const seq = ++this._citySeq;
    try {
      const idx = await BkkLib.withIndexes(this._config).cityIndex();
      if (seq !== this._citySeq) return;
      const ops = (idx.ops || []).slice().sort((a, b) => a.name.localeCompare(b.name, "hu"));
      const cur = this._config.city || "";
      const lang = this._lang();
      const signature = lang + ":" + ops.map((o) => o.id).join("|");
      if (sel.dataset.ops === signature && sel.options.length > 1) {
        if (cur && sel.value !== cur) sel.value = cur;
        return;
      }
      const ph = t(lang, "cityPlaceholder");
      sel.innerHTML = `<option value="">${BkkLib.esc(ph)}</option>` + ops.map((o) => `<option value="${BkkLib.esc(o.id)}">${BkkLib.esc(o.name)}</option>`).join("");
      sel.dataset.ops = signature;
      if (cur) sel.value = cur;
    } catch (err) {
      if (seq !== this._citySeq) return;
      this._error(err);
    }
  }
  /* An error is kept as thrown and worded in the editor's language, now
     and after a language switch. */
  _error(err) {
    this._lastErr = err || "";
    this._syncErr();
  }
  _syncErr() {
    const el = this._el("err");
    if (el) el.textContent = this._lastErr ? errText(this._lang(), this._lastErr) : "";
  }
  /* undefined in a patch removes the key, so a new pick leaves nothing of
     the old stop (coordinates, kind) behind in the card's YAML. */
  _emit(patch) {
    const cfg = Object.assign({}, this._config, patch);
    Object.keys(patch || {}).forEach((k) => {
      if (patch[k] === void 0) delete cfg[k];
    });
    this._config = cfg;
    this.dispatchEvent(new CustomEvent("config-changed", {
      bubbles: true,
      composed: true,
      detail: { config: cfg }
    }));
  }
  /* One lookup at a time: a search typed while the hass setter's lookup
     runs waits for that one instead of starting a second (and a second
     _loadDests). */
  _fillKey() {
    if (!this._fillKeyP) {
      this._fillKeyP = this._fillKeyRun().finally(() => {
        this._fillKeyP = null;
      });
    }
    return this._fillKeyP;
  }
  async _fillKeyRun() {
    if (this._keyless()) return;
    this._loadingKey = true;
    try {
      const key = await BkkLib.apiKeyFromDashboard(this._hass);
      if (key && !this._config.apiKey && !this._userEditedKey) {
        this._borrowedKey = key;
        const stopId = this._config.stopId;
        if (stopId && this._destsStale(stopId)) this._loadDests();
        await this._probeKey();
      }
    } finally {
      this._loadingKey = false;
    }
  }
  async _probeKey() {
    const mode = this._mode();
    if (mode === "volan" || mode === "helyi") {
      this._syncApiKey();
      return;
    }
    const access = this._keyAccess();
    if (!access) {
      this._keyOk = false;
      this._syncApiKey();
      return;
    }
    if (this._probingKey) return;
    if (this._probedKey === access && (this._keyOk != null || Date.now() < this._probeRetryAt)) {
      this._syncApiKey();
      return;
    }
    this._probingKey = true;
    this._probedKey = access;
    try {
      const result = await BkkLib.probeApiKey(this._apiKey());
      if (this._keyAccess() !== access) return;
      const unreached = !result.ok && result.code === "errApiKeyProbe";
      this._keyOk = unreached ? null : !!result.ok;
      this._probeRetryAt = unreached ? Date.now() + this._probeRetryMs : 0;
      this._syncApiKey();
      if (!result.ok && result.code) this._error(codedError(result.code));
      else if (result.ok) this._error("");
    } finally {
      this._probingKey = false;
      const latest = this._keyAccess();
      if (latest && latest !== this._probedKey) this._probeKey();
    }
  }
  async _search(q, which) {
    const box = this._el(which === "dest" ? "destHits" : "hits");
    if (!box) return;
    if (which === "dest") {
      this._paintDestHits(q);
      return;
    }
    const seq = ++this._searchSeq;
    if (!q || q.length < 2) {
      box.innerHTML = "";
      return;
    }
    try {
      this._error("");
      if (!this._hasFutar() && !this._userEditedKey) await this._fillKey();
      if (seq !== this._searchSeq) return;
      if (!this._hasFutar() && this._needsKey()) {
        box.innerHTML = "";
        this._error(codedError("errNoApiKey"));
        return;
      }
      const mode = this._mode();
      const hits = await BkkLib.withIndexes(this._config).searchStops(this._apiKey(), q, mode, this._config.city);
      if (seq !== this._searchSeq) return;
      if (!hits.length) {
        box.innerHTML = '<span class="hint">' + BkkLib.esc(this._modeCopy(mode).empty) + "</span>";
        return;
      }
      box.innerHTML = hits.map((s) => `<button class="hit" type="button" data-id="${BkkLib.esc(s.id)}">${BkkLib.esc(s.label || s.name)}</button>`).join("");
      box.querySelectorAll(".hit").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.getAttribute("data-id");
          const st = hits.find((h) => h.id === id);
          if (st) this._pickStop(st, which);
        });
      });
    } catch (err) {
      if (seq !== this._searchSeq) return;
      this._error(err);
    }
  }
  /* Rank the reachable destinations by how much of the query they share, so an
     unreachable search still ends with something usable. */
  _suggestDests(f) {
    const dests = this._dests || [];
    if (!dests.length) return [];
    if (!f || f.length < 3) return dests.slice(0, 6);
    const grams = /* @__PURE__ */ new Set();
    for (let i = 0; i + 3 <= f.length; i++) grams.add(f.slice(i, i + 3));
    const scored = dests.map((d) => {
      const fold = BkkLib.fold(d.name);
      let score = 0;
      grams.forEach((g) => {
        if (fold.indexOf(g) >= 0) score += 1;
      });
      return { d, score };
    }).filter((x) => x.score > 0);
    scored.sort((a, b) => b.score - a.score);
    const picked = scored.slice(0, 6).map((x) => x.d);
    return picked.length ? picked : dests.slice(0, 6);
  }
  _paintDestHits(q) {
    const box = this._el("destHits");
    if (!box) return;
    const copy = this._modeCopy(this._mode());
    if (!this._config.stopId) {
      box.innerHTML = '<span class="hint">' + BkkLib.esc(copy.destNeedOrigin) + "</span>";
      return;
    }
    if (this._destsLoading) {
      box.innerHTML = '<span class="hint">' + BkkLib.esc(t(this._lang(), "destLoading")) + "</span>";
      return;
    }
    if (!this._dests.length) {
      box.innerHTML = '<span class="hint">' + BkkLib.esc(copy.destNone) + "</span>";
      return;
    }
    const f = BkkLib.fold(q || "");
    const hits = f ? this._dests.filter((d) => BkkLib.fold(d.name).indexOf(f) >= 0) : this._dests;
    if (!hits.length) {
      const alts = this._suggestDests(f);
      const msg = alts.length ? t(this._lang(), "destEmptySuggest", alts.map((d) => d.name).join(", ")) : copy.destEmpty;
      box.innerHTML = '<span class="hint">' + BkkLib.esc(msg) + "</span>";
      return;
    }
    const shown = hits.slice(0, 50);
    box.innerHTML = shown.map((s) => `<button class="hit" type="button" data-key="${BkkLib.esc(s.key)}">${BkkLib.esc(s.name)}</button>`).join("");
    box.querySelectorAll(".hit").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-key");
        const st = this._dests.find((d) => d.key === key);
        if (!st) return;
        this._pickStop(st, "dest");
      });
    });
  }
  async _loadDests() {
    if (!this._config.stopId) return;
    if (!this._canLoadDests()) return;
    const stopId = this._config.stopId;
    const access = this._keyAccess();
    const seq = ++this._destSeq;
    this._destsLoading = true;
    this._loadingStop = stopId;
    const box = this._el("destHits");
    if (box) box.innerHTML = '<span class="hint">' + BkkLib.esc(t(this._lang(), "destLoading")) + "</span>";
    try {
      this._error("");
      const idx = await BkkLib.withIndexes(this._config).reachableDests(
        this._apiKey(),
        this._cache,
        { id: this._config.stopId, name: this._config.stopName },
        this._mode(),
        this._config.city
      );
      if (seq !== this._destSeq || this._config.stopId !== stopId) return;
      this._loadedStop = stopId;
      this._loadedAccess = access;
      this._destsLoading = false;
      this._loadingStop = "";
      this._dests = idx.dests || [];
      this._destRoutes = idx.destRoutes || {};
      const cur = this._config.routeIds || [];
      if (this._config.destKey && this._destRoutes[this._config.destKey] && !cur.length) {
        this._emit({ routeIds: this._destRoutes[this._config.destKey].slice() });
      }
      const dq = this._el("dq");
      this._paintDestHits(dq ? dq.value.trim() : "");
    } catch (err) {
      if (seq !== this._destSeq) return;
      this._loadedStop = "";
      this._error(err);
      if (box) box.innerHTML = "";
    } finally {
      if (seq === this._destSeq) {
        this._destsLoading = false;
        this._loadingStop = "";
      }
    }
  }
  /* What a pick knows of a stop: its id, coordinates and kind replace the
     old stop's, or clear them. Hand-written stopLat / destKind left over from
     an earlier stop would otherwise keep placing pins and Transitous
     lookups there, and the planner's swap needs destStopId. */
  _stopPatch(stop, prefix) {
    const num = (v) => v != null && v !== "" && Number.isFinite(Number(v)) ? Number(v) : void 0;
    const lat = num(stop && stop.lat);
    const lon = num(stop && stop.lon);
    const both = lat !== void 0 && lon !== void 0;
    return {
      [`${prefix}Lat`]: both ? lat : void 0,
      [`${prefix}Lon`]: both ? lon : void 0,
      [`${prefix}Kind`]: stop && stop.kind ? String(stop.kind) : void 0
    };
  }
  /* Both stops gone (mode or city change). */
  _noStops() {
    return Object.assign({
      stopId: "",
      stopName: "",
      destKey: "",
      destName: "",
      destStopId: void 0,
      routeIds: []
    }, this._stopPatch(null, "stop"), this._stopPatch(null, "dest"));
  }
  /* The hop card's title names the pair it was set up for. */
  _autoName() {
    return true;
  }
  _pairTitle(stopName, destName) {
    return stopName && destName ? `${stopName} → ${destName}` : "";
  }
  /* A title the editor wrote (or one reading just like it) follows the
     next pair; a title the user typed stays. */
  _titleIsAuto(name) {
    if (!name) return true;
    if (this._autoTitle && name === this._autoTitle) return true;
    return name === this._pairTitle(this._config.stopName, this._config.destName);
  }
  /* The old pair's title, if the editor wrote it, is replaced once the
     next pair is picked: remembered here, before the pair is cleared and
     _titleIsAuto can no longer tell it from a typed one. */
  _keepAutoTitle() {
    if (this._config.name && this._titleIsAuto(this._config.name)) this._autoTitle = this._config.name;
  }
  _pickStop(stop, which) {
    this._cancelSearches();
    if (which === "dest") {
      const box2 = this._el("destHits");
      const dq2 = this._el("dq");
      if (box2) box2.innerHTML = "";
      if (dq2) dq2.value = stop.label || stop.name;
      const key = stop.key || BkkLib.norm(stop.name);
      const patch2 = Object.assign({
        destKey: key,
        destName: stop.name,
        destStopId: stop.id ? String(stop.id) : void 0,
        routeIds: (this._destRoutes[key] || []).slice()
      }, this._stopPatch(stop, "dest"));
      if (this._autoName() && this._config.stopName && this._titleIsAuto(this._config.name || "")) {
        patch2.name = this._pairTitle(this._config.stopName, stop.name);
        this._autoTitle = patch2.name;
      }
      this._emit(patch2);
      this._syncName();
      this._syncDestLabel();
      return;
    }
    const box = this._el("hits");
    const q = this._el("q");
    if (box) box.innerHTML = "";
    if (q) q.value = stop.label || stop.name;
    this._keepAutoTitle();
    this._cache = {};
    this._dests = [];
    this._destRoutes = {};
    this._loadedStop = "";
    const patch = Object.assign({}, this._noStops(), {
      stopId: stop.id,
      stopName: stop.name
    }, this._stopPatch(stop, "stop"));
    this._emit(patch);
    this._syncStopLabel();
    this._syncDestLabel();
    const dq = this._el("dq");
    if (dq) dq.value = "";
  }
};

// src/hop-card.js
var leafletPromise = null;
var NARROW_ROWS_PX = 440;
var BKKHopCard = class extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._configDataKey = null;
    this._poll = null;
    this._tick = null;
    this._onVisibility = null;
    this._hass = null;
    this._tripCache = {};
    this._rows = [];
    this._rawRows = [];
    this._err = "";
    this._loading = false;
    this._rowsFilled = false;
    this._painted = false;
    this._gen = 0;
    this._keyTried = false;
    this._reloadActive = false;
    this._mapOverlay = null;
    this._map = null;
    this._mapMarker = null;
    this._mapBoardMarker = null;
    this._mapAlightMarker = null;
    this._mapRoute = null;
    this._openMapKey = null;
    this._onMapKey = null;
    this._mapClicksBound = false;
    this._openingMap = "";
    this._mapRef = null;
    this._mapState = null;
    this._mapL = null;
    this._mapReturnFocus = null;
    this._hiddenAt = 0;
    this._pollRun = null;
  }
  static async getConfigElement() {
    if (!customElements.get(BKK_HOP_EDITOR)) await customElements.whenDefined(BKK_HOP_EDITOR);
    return document.createElement(BKK_HOP_EDITOR);
  }
  static getStubConfig() {
    return {
      name: "",
      stopId: "",
      stopName: "",
      routeIds: [],
      destKey: "",
      destName: "",
      mav: false,
      volan: false,
      helyi: false,
      city: "",
      language: "auto",
      minutesAfter: DEFAULT_MINUTES_AFTER
    };
  }
  _lang() {
    return resolveLang((this._config || {}).language, this._hass);
  }
  setConfig(config) {
    if (!this.shadowRoot) {
      try {
        this.attachShadow({ mode: "open" });
      } catch (_e) {
      }
    }
    if (this.isConnected) this._holdGate();
    const next = normalizeCardConfig(config);
    const prevKey = this._configDataKey != null ? configDataKey(this._config) : null;
    const nextKey = configDataKey(next);
    this._config = next;
    this._configDataKey = nextKey;
    BkkLib.warnCardApiKey(next);
    if (prevKey != null && prevKey === nextKey && this._painted) {
      try {
        this._render();
      } catch (err) {
        this._showErr(err);
      }
      return;
    }
    this._tripCache = {};
    this._err = "";
    this._keyTried = false;
    this._rows = [];
    this._rawRows = [];
    this._pollRun = null;
    try {
      this._render();
    } catch (err) {
      this._showErr(err);
      return;
    }
    if (this.isConnected) {
      this._reloadSafe();
      return;
    }
    this._gen += 1;
    this._reloadActive = false;
  }
  /* _reload is async, so its rejections need an owner. Mark in-flight
     synchronously: a detach and re-attach in the same turn (Home Assistant
     re-parenting a card) must not start a second fetch. */
  _reloadSafe() {
    this._reloadActive = true;
    Promise.resolve().then(() => this._reload()).catch((err) => this._showErr(err));
  }
  /* The card's own key, or a key borrowed from a sibling card while the
     integration has none. Empty when FUTÁR goes through the integration. */
  _apiKey() {
    return BkkLib.cardApiKey(this._config, this._hass, this._borrowedKey);
  }
  _hasFutar() {
    return BkkLib.hasFutar(this._apiKey(), this._hass);
  }
  set hass(hass) {
    const first = !this._hass;
    const langBefore = this._hass ? this._lang() : "";
    const viaBefore = this._viaIntegration;
    this._hass = hass;
    BkkLib._hass = hass;
    if (this._lang() !== langBefore) this._langChanged();
    this._viaIntegration = !(this._config || {}).apiKey && BkkLib.integrationHasKey(hass);
    if (this._viaIntegration && !viaBefore && this._painted && !this._reloadActive && this.isConnected) this._reloadSafe();
    if (first && this._rawRows && this._rawRows.length) {
      BkkLib.enrichFromHass(this._hass, this._rawRows);
      const cfg = this._config || {};
      this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
      this._paint();
    }
    this._maybeFillKey();
  }
  /* With `language: auto` Home Assistant calls setConfig before hass, so the
     first paint is in the browser's language: redraw in the user's. */
  _langChanged() {
    this._paint();
  }
  /* hass is set on every state change, so looking for a sibling card's key has
     to happen at most once, and never in the key-less modes. */
  _maybeFillKey() {
    const mode = BkkLib.mode(this._config || {});
    if (mode === "volan" || mode === "helyi") return;
    if (this._config.apiKey || this._keyTried || BkkLib.integrationHasKey(this._hass)) return;
    this._keyTried = true;
    this._fillKey().catch(() => {
    });
  }
  getCardSize() {
    const rows = Math.max(1, (this._rows || []).length);
    return Math.max(1, Math.round((44 + rows * 30) / 50));
  }
  /* Sections view: a departure board needs half a 12-column row at least to
     keep the headsign readable; its height follows the rows. */
  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }
  /* Home Assistant re-parents cards while editing a dashboard, so the timers
     have to come back after a detach. */
  _holdGate() {
    if (this._gateHeld) return;
    this._gateHeld = true;
    BkkLib._gateCards += 1;
  }
  connectedCallback() {
    this._holdGate();
    this._bindVisibility();
    if (!this._painted) return;
    this._startTick();
    this._bindMapClicks();
    if (!this._poll && !this._reloadActive && this._shouldReloadOnConnect()) this._reloadSafe();
  }
  _pageHidden() {
    return typeof document !== "undefined" && document.visibilityState === "hidden";
  }
  _bindVisibility() {
    if (this._onVisibility || typeof document === "undefined") return;
    this._onVisibility = () => this._visibilityChanged();
    document.addEventListener("visibilitychange", this._onVisibility);
  }
  _unbindVisibility() {
    if (!this._onVisibility || typeof document === "undefined") return;
    document.removeEventListener("visibilitychange", this._onVisibility);
    this._onVisibility = null;
  }
  /* A background tab must not poll the APIs or repaint countdowns nobody
     sees. Back in front: the timers again, and a fresh run only after an
     absence longer than the poll interval. A quick tab switch would
     otherwise refetch everything each time; the poll picks up instead. */
  _visibilityChanged() {
    if (this._pageHidden()) {
      if (!this._hiddenAt) this._hiddenAt = Date.now();
      if (this._poll) {
        clearInterval(this._poll);
        this._poll = null;
      }
      if (this._tick) {
        clearInterval(this._tick);
        this._tick = null;
      }
      return;
    }
    const away = this._hiddenAt ? Date.now() - this._hiddenAt : Infinity;
    this._hiddenAt = 0;
    if (!this.isConnected || !this._painted) return;
    this._startTick();
    this._tickPaint();
    if (this._poll || this._reloadActive || !this._shouldReloadOnConnect()) return;
    if (away < refreshSeconds((this._config || {}).refresh) * 1e3 && this._resumePoll()) return;
    this._reloadSafe();
  }
  /* The poll repeats the reload's own run. A run slower than the interval
     (a stalled ELVIRA call) skips the next tick instead of racing it: two
     runs could paint out of order. The busy flag lives with the run, so a
     poll picked up again after a tab switch still waits for it. */
  _armPoll(pollRun) {
    if (this._poll) {
      clearInterval(this._poll);
      this._poll = null;
    }
    this._pollRun = pollRun;
    const cfg = this._config || {};
    const every = refreshSeconds(cfg.refresh) * 1e3;
    this._poll = setInterval(() => {
      if (pollRun.busy) return;
      pollRun.busy = true;
      pollRun.run().finally(() => {
        pollRun.busy = false;
      });
    }, every);
  }
  /* The poll of the current reload again, if there is one to pick up. */
  _resumePoll() {
    const p = this._pollRun;
    if (!p || p.gen !== this._gen || !this._shouldPoll()) return false;
    this._armPoll(p);
    return true;
  }
  _shouldPoll() {
    return true;
  }
  _shouldReloadOnConnect() {
    return true;
  }
  disconnectedCallback() {
    this._unbindVisibility();
    if (this._gateHeld) {
      this._gateHeld = false;
      BkkLib._gateCards = Math.max(0, BkkLib._gateCards - 1);
    }
    if (this._poll) {
      clearInterval(this._poll);
      this._poll = null;
    }
    if (this._tick) {
      clearInterval(this._tick);
      this._tick = null;
    }
    this._closeVehicleMap();
  }
  /* Keep the rows on screen and put the failure in a banner above them: a
     single failed refresh should not wipe a timetable that is still useful. */
  _showErr(err) {
    this._err = err || "";
    this._paint();
  }
  /* The error is kept as thrown and worded at paint, so a language change
     (hass arriving, the editor's switch) rewords the banner too. */
  _errMessage(lang) {
    return this._err ? errText(lang || this._lang(), this._err) : "";
  }
  /* Kept apart from the config: once the integration's futar service shows
     up (HA still starting, entry reloading), it wins over a borrowed key. */
  async _fillKey() {
    const key = await BkkLib.apiKeyFromDashboard(this._hass);
    if (!key || this._config.apiKey) return;
    this._borrowedKey = key;
    if (this.isConnected) this._reloadSafe();
  }
  _header() {
    const cfg = this._config || {};
    const ready = cfg.stopId && cfg.destKey;
    if (cfg.name) return cfg.name;
    if (ready) return `${cfg.stopName || ""} → ${cfg.destName || ""}`;
    return t(this._lang(), "cardTitle");
  }
  _render() {
    if (!this._rows) this._rows = [];
    if (!this._tripCache) this._tripCache = {};
    const root = this.shadowRoot;
    if (!root) return;
    if (this._painted) {
      this._paint();
      return;
    }
    while (root.firstChild) root.removeChild(root.firstChild);
    const style = document.createElement("style");
    style.textContent = `
      :host { display: block; pointer-events: auto; }
      ha-card { overflow: visible; pointer-events: auto; }
      .wrap { padding: 10px 12px 11px; }
      .head { font-weight: 700; margin-bottom: 7px; overflow: hidden;
        text-overflow: ellipsis; white-space: nowrap; }
      .err { font-size: 12px; color: var(--error-color, #e66); margin-bottom: 6px; }
      .msg { font-size: 12px; color: var(--secondary-text-color); }
      .body { pointer-events: auto; container: ht-deps / inline-size; }
      table { width: 100%; border-collapse: collapse; font-size: 13px; pointer-events: auto; }
      tbody tr, tr.row-clickable { cursor: pointer; }
      tr.row-clickable:hover td { background: color-mix(in srgb, var(--primary-color) 8%, transparent); }
      tr + tr td { border-top: 1px solid var(--divider-color); }
      td { padding: 6px 3px; vertical-align: middle; }
      td.icon { width: 20px; color: var(--secondary-text-color); }
      td.icon ha-icon, td.mapbtn ha-icon { --mdc-icon-size: 18px; display: block; pointer-events: none; }
      td.mapbtn { width: 28px; color: var(--secondary-text-color); }
      td.mapbtn .map-open {
        appearance: none; border: 0; background: transparent; color: inherit;
        padding: 2px; margin: 0; cursor: pointer; display: flex;
        align-items: center; justify-content: center; pointer-events: auto;
      }
      td.route { width: 1%; white-space: nowrap; }
      .badge { display: inline-block; min-width: 2.4em; padding: 2px 6px;
        border-radius: 6px; font-weight: 800; font-size: 12px; line-height: 1.35;
        text-align: center; }
      .badge.long { font-size: 10px; padding: 2px 5px; letter-spacing: -0.02em; }
      td.dest { max-width: 0; overflow: hidden; white-space: nowrap;
        text-overflow: ellipsis; }
      .num { margin-left: 5px; font-size: 11px; color: var(--secondary-text-color); }
      td.flags { width: 1%; white-space: nowrap; }
      td.when { width: 1%; white-space: nowrap; text-align: right;
        font-variant-numeric: tabular-nums; }
      .at { font-weight: 700; }
      .at.late { color: var(--error-color, #e66); }
      .at.early { color: var(--success-color, #2e9e4f); }
      .sched { margin-left: 5px; font-size: 11px; text-decoration: line-through;
        color: var(--secondary-text-color); }
      td.in { width: 1%; white-space: nowrap; text-align: right; font-weight: 700;
        font-variant-numeric: tabular-nums; }
      .travel { display: block; font-size: 11px; font-weight: 400;
        color: var(--secondary-text-color); }
      .pictos { display: inline-flex; gap: 4px; align-items: center; }
      .picto { position: relative; font-size: 11px; line-height: 1.2; cursor: help; }
      .picto.booking { color: #c0392b; font-weight: 800; font-size: 13px; }
      .picto.pass {
        padding: 0 3px; border-radius: 2px; background: #2e7d32; color: #fff;
        font-size: 10px; font-weight: 800; letter-spacing: 0.02em; line-height: 1.45;
      }
      .plat {
        display: inline-block; margin-right: 4px; padding: 0 5px; border-radius: 2px;
        background: #2E5EA8; color: #fff; font-size: 11px; font-weight: 700;
        line-height: 1.45; white-space: nowrap;
      }
      .plat.stand {
        background: none; color: var(--secondary-text-color); font-weight: 600;
        box-shadow: inset 0 0 0 1px var(--divider-color, rgba(127, 127, 127, 0.5));
      }
      .picto::after {
        content: attr(data-tip); position: absolute; left: 50%; top: calc(100% + 7px);
        transform: translateX(-50%); background: var(--primary-text-color, #222);
        color: var(--card-background-color, #fff); padding: 4px 7px; border-radius: 6px;
        font-size: 11px; font-weight: 500; white-space: nowrap; pointer-events: none;
        opacity: 0; visibility: hidden; transition: opacity 0.12s; z-index: 30;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
      }
      .picto::before {
        content: ''; position: absolute; left: 50%; top: calc(100% + 2px);
        transform: translateX(-50%); border: 5px solid transparent;
        border-bottom-color: var(--primary-text-color, #222); pointer-events: none;
        opacity: 0; visibility: hidden; transition: opacity 0.12s; z-index: 30;
      }
      .picto:hover::after, .picto:hover::before,
      .picto:focus-visible::after, .picto:focus-visible::before {
        opacity: 1; visibility: visible;
      }
      /* On a phone the fixed columns (badge, pictograms, map pin, time,
         countdown) left the headsign a letter or two. Below ${NARROW_ROWS_PX}px each
         row becomes two lines on a shared grid: badge over vehicle icon, the
         headsign across the middle with pictograms and the map pin under it,
         and the time columns still aligned from row to row. */
      @container ht-deps (max-width: ${NARROW_ROWS_PX}px) {
        table { display: block; }
        tbody {
          display: grid; column-gap: 8px;
          grid-template-columns: auto minmax(0, 1fr) auto auto auto;
        }
        /* Without subgrid each row keeps the same tracks, just unaligned. */
        tr {
          display: grid; grid-column: 1 / -1;
          grid-template-columns: auto minmax(0, 1fr) auto auto auto;
          grid-template-columns: subgrid;
          grid-template-rows: auto auto; row-gap: 2px; align-items: center;
          padding: 6px 0;
        }
        tr + tr { border-top: 1px solid var(--divider-color); }
        tr + tr td { border-top: 0; }
        tr.row-clickable:hover td { background: none; }
        tr.row-clickable:hover { background: color-mix(in srgb, var(--primary-color) 8%, transparent); }
        td { padding: 0; }
        td.icon, td.route, td.flags, td.mapbtn, td.when, td.in { width: auto; }
        td.route { grid-area: 1 / 1; justify-self: center; }
        td.icon { grid-area: 2 / 1; justify-self: center; }
        td.dest {
          grid-area: 1 / 2 / 2 / 4; min-width: 0; max-width: none;
          white-space: normal; overflow-wrap: break-word; line-height: 1.3;
          display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
        }
        .num { white-space: nowrap; }
        td.flags { grid-area: 2 / 2; min-width: 0; white-space: normal; }
        .pictos { flex-wrap: wrap; }
        td.mapbtn { grid-area: 2 / 3; }
        td.when { grid-area: 1 / 4 / 3 / 5; align-self: start; }
        td.in { grid-area: 1 / 5 / 3 / 6; align-self: start; }
        .sched { display: block; margin-left: 0; }
      }
    `;
    const card = document.createElement("ha-card");
    const wrap = document.createElement("div");
    wrap.className = "wrap";
    this._elHead = document.createElement("div");
    this._elHead.className = "head";
    this._elErr = document.createElement("div");
    this._elErr.className = "err";
    this._elBody = document.createElement("div");
    this._elBody.className = "body";
    wrap.appendChild(this._elHead);
    wrap.appendChild(this._elErr);
    wrap.appendChild(this._elBody);
    card.appendChild(wrap);
    root.appendChild(style);
    root.appendChild(card);
    this._painted = true;
    this._bindMapClicks();
    this._paint();
    this._startTick();
  }
  /* The countdown is derived from the epoch seconds, so it stays honest
     between refreshes without hitting the API again. */
  _startTick() {
    if (this._tick) clearInterval(this._tick);
    this._tick = null;
    if (this._pageHidden() || !this.isConnected) return;
    this._tick = setInterval(() => this._tickPaint(), 15e3);
  }
  /* Between refreshes only the countdowns change. Rewriting the whole table
     every 15 s dropped hover, keyboard focus and open tooltips, so update the
     text in place and repaint only when a departure falls off the list. */
  _tickPaint() {
    const shown = this._shownRows || [];
    if (!shown.length || !this._elBody) return;
    const nowSec = Math.floor(Date.now() / 1e3);
    const due = (this._rows || []).filter((r) => departureStillDue(r.depTs, nowSec));
    if (due.length !== shown.length || due.some((r, i) => r !== shown[i])) {
      this._paint();
      return;
    }
    const lang = this._lang();
    this._elBody.querySelectorAll("tr[data-row-index]").forEach((tr) => {
      const row = shown[Number(tr.getAttribute("data-row-index"))];
      const cell = tr.querySelector(".cd");
      if (!row || !cell) return;
      const text = this._countdown(row.depTs, lang);
      if (cell.textContent !== text) cell.textContent = text;
    });
  }
  _paint() {
    if (!this._painted || !this._elBody) return;
    const lang = this._lang();
    const cfg = this._config || {};
    this._elHead.textContent = this._header();
    const errMsg = this._errMessage(lang);
    this._elErr.textContent = errMsg;
    this._elErr.style.display = errMsg ? "" : "none";
    const nowSec = Math.floor(Date.now() / 1e3);
    const allRows = this._rows || [];
    const rows = allRows.filter((r) => departureStillDue(r.depTs, nowSec));
    this._shownRows = rows;
    if (rows.length) {
      this._elBody.innerHTML = `<table><tbody>${rows.map((r, i) => this._rowHtml(r, lang, i)).join("")}</tbody></table>`;
      return;
    }
    const msg = this._emptyBodyMessage(lang, cfg);
    this._elBody.innerHTML = msg ? `<div class="msg">${BkkLib.esc(msg)}</div>` : "";
  }
  _emptyBodyMessage(lang, cfg) {
    if (!cfg.stopId || !cfg.destKey) return t(lang, "cardPickStops");
    if (this._loading) return t(lang, "cardLoading");
    if (this._err) return "";
    return t(lang, "cardNoDepartures");
  }
  _badgeStyle(row) {
    const hex = (v, fallback) => {
      const s = String(v || "").replace("#", "");
      return /^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$/.test(s) ? "#" + s : fallback;
    };
    const bg = hex(row.color, "var(--primary-color)");
    const fg = hex(row.textcolor, "#fff");
    return `background:${bg};color:${fg}`;
  }
  _picto(glyph, tip, cls) {
    const extra = cls ? ` ${cls}` : "";
    return `<span class="picto${extra}" tabindex="0" role="img" aria-label="${BkkLib.esc(tip)}" data-tip="${BkkLib.esc(tip)}">${BkkLib.esc(glyph)}</span>`;
  }
  _countdown(ts, lang) {
    if (!ts) return "";
    const mins = Math.round((ts * 1e3 - Date.now()) / 6e4);
    return mins <= 0 ? t(lang, "now") : t(lang, "minutes", mins);
  }
  _rowHtml(r, lang, idx) {
    const esc = BkkLib.esc;
    const delayMin = Math.round(Number(r.delay || 0) / 60);
    let atClass = "at";
    let sched = "";
    let atTip = "";
    if (delayMin >= 1) {
      atClass += " late";
      atTip = t(lang, "tipDelay", delayMin);
      if (r.attime && r.attime !== r.predicted_attime) sched = r.attime;
    } else if (delayMin <= -1) {
      atClass += " early";
      atTip = t(lang, "tipEarly", Math.abs(delayMin));
      if (r.attime && r.attime !== r.predicted_attime) sched = r.attime;
    } else if (r.attime) {
      atTip = t(lang, "tipScheduled", r.attime);
    }
    const pictos = [];
    if (r.wheelchair) pictos.push(this._picto("♿", t(lang, "tipWheelchair")));
    if (r.bikesAllowed) pictos.push(this._picto("🚲", t(lang, "tipBike")));
    if (r.booking) {
      const tip2 = r.reservationFreeSection ? `${t(lang, "tipBooking")} · ${t(lang, "tipBookingFreeSection")}` : t(lang, "tipBooking");
      pictos.push(this._picto("H", tip2, "booking"));
      if (r.nationalPass) pictos.push(this._picto("OB", t(lang, "tipNationalPass"), "pass"));
    }
    const rail = BkkLib.isTrainRow(r);
    const plat = r.platform ? `<span class="plat${rail ? "" : " stand"}" title="${esc(t(lang, rail ? "tipPlatform" : "tipStand", r.platform))}">${esc(r.platform)}</span>` : "";
    const travel = r.travelMin ? `<span class="travel" title="${esc(t(lang, "tipTravel", r.travelMin))}">${esc(t(lang, "minutes", r.travelMin))}</span>` : "";
    const num = r.trainNumber ? `<span class="num" title="${esc(t(lang, "tipTrain", r.trainNumber))}">${esc(r.trainNumber)}</span>` : "";
    const tip = esc(t(lang, "mapOpenTip"));
    const clickable = ` class="row-clickable" data-row-index="${idx}" title="${tip}"`;
    const mapBtn = `<td class="mapbtn"><button type="button" class="map-open" aria-label="${tip}" title="${tip}"><ha-icon icon="mdi:map-marker-outline"></ha-icon></button></td>`;
    return `<tr${clickable}>
      <td class="icon"><ha-icon icon="${esc(r.icon || "mdi:bus")}"></ha-icon></td>
      <td class="route"><span class="badge${String(r.label || "").length > 4 ? " long" : ""}" style="${esc(this._badgeStyle(r))}">${esc(r.label || "?")}</span></td>
      <td class="dest" title="${esc(r.headsign || "")}">${esc(r.headsign || "")}${num}</td>
      <td class="flags">${plat}${pictos.length ? `<span class="pictos">${pictos.join("")}</span>` : ""}</td>
      ${mapBtn}
      <td class="when"><span class="${atClass}" title="${esc(atTip)}">${esc(r.predicted_attime || r.attime || "")}</span>${sched ? `<span class="sched">${esc(sched)}</span>` : ""}</td>
      <td class="in"><span class="cd">${esc(this._countdown(r.depTs, lang))}</span>${travel}</td>
    </tr>`;
  }
  _bindMapClicks() {
    if (this._mapClicksBound) return;
    if (!this._elBody) return;
    this._mapClicksBound = true;
    this.addEventListener("click", (ev) => this._onRowClick(ev), true);
  }
  _rowFromClick(ev) {
    const path = typeof ev.composedPath === "function" ? ev.composedPath() : [];
    let tr = null;
    for (let i = 0; i < path.length; i++) {
      const n = path[i];
      if (n && n.nodeType === 1 && n.getAttribute && n.getAttribute("data-row-index") != null && (n.tagName === "TR" || n.classList && n.classList.contains("row-clickable"))) {
        tr = n;
        break;
      }
    }
    if (!tr) {
      const start = ev.target;
      if (start && start.closest) tr = start.closest("tr[data-row-index]");
    }
    if (!tr) return null;
    const idx = Number(tr.getAttribute("data-row-index"));
    return (this._shownRows || [])[idx] || null;
  }
  _onRowClick(ev) {
    const row = this._rowFromClick(ev);
    if (!row) return;
    ev.preventDefault();
    ev.stopPropagation();
    this._openVehicleMap(row);
  }
  _mapTypeLabel(kind, lang) {
    const k = String(kind || "").toLowerCase();
    if (k === "tram") return t(lang, "mapTypeTram");
    if (k === "trolleybus" || k === "trolley") return t(lang, "mapTypeTrolley");
    if (k === "subway" || k === "metro") return t(lang, "mapTypeSubway");
    if (k === "rail" || k === "train") return t(lang, "mapTypeRail");
    if (k === "coach") return t(lang, "mapTypeCoach");
    if (k === "bus") return t(lang, "mapTypeBus");
    if (k === "ferry") return t(lang, "mapTypeFerry");
    return t(lang, "mapTypeVehicle");
  }
  _mapGlyph(kind) {
    const k = String(kind || "").toLowerCase();
    if (k === "tram") return "🚊";
    if (k === "trolleybus" || k === "trolley") return "🚎";
    if (k === "subway" || k === "metro") return "🚇";
    if (k === "rail" || k === "train") return "🚆";
    if (k === "bus" || k === "coach") return "🚌";
    if (k === "ferry") return "⛴";
    return "📍";
  }
  _ensureLeaflet() {
    const isLeaflet = (L) => !!(L && typeof L.map === "function" && typeof L.tileLayer === "function" && typeof L.divIcon === "function");
    if (typeof window !== "undefined" && isLeaflet(window.L)) return Promise.resolve(window.L);
    if (leafletPromise) return leafletPromise;
    leafletPromise = new Promise((resolve, reject) => {
      const cssId = "hungarian-transport-leaflet-css";
      if (typeof document !== "undefined" && !document.getElementById(cssId)) {
        const link = document.createElement("link");
        link.id = cssId;
        link.rel = "stylesheet";
        link.href = new URL("leaflet/leaflet.css", ASSET_BASE).href;
        document.head.appendChild(link);
      }
      if (typeof window !== "undefined" && isLeaflet(window.L)) {
        resolve(window.L);
        return;
      }
      const existing = typeof document !== "undefined" ? document.getElementById("hungarian-transport-leaflet-js") : null;
      if (existing) {
        existing.addEventListener("load", () => {
          if (isLeaflet(window.L)) resolve(window.L);
          else reject(new Error("leaflet"));
        });
        existing.addEventListener("error", reject);
        return;
      }
      const script = document.createElement("script");
      script.id = "hungarian-transport-leaflet-js";
      script.src = new URL("leaflet/leaflet.js", ASSET_BASE).href;
      script.onload = () => {
        if (isLeaflet(window.L)) resolve(window.L);
        else reject(new Error("leaflet"));
      };
      script.onerror = reject;
      document.head.appendChild(script);
    });
    leafletPromise.catch(() => {
      leafletPromise = null;
      const el = typeof document !== "undefined" ? document.getElementById("hungarian-transport-leaflet-js") : null;
      if (el && !isLeaflet(window.L)) el.remove();
    });
    return leafletPromise;
  }
  /* Every close (and so every new open) bumps the map sequence. An open that
     is still awaiting Leaflet or a route checks it after each await, so a
     superseded open never adds a second overlay that nothing can close. */
  _closeVehicleMap() {
    this._mapSeq = (this._mapSeq || 0) + 1;
    const hadOverlay = !!this._mapOverlay;
    if (this._onMapKey && typeof document !== "undefined") {
      document.removeEventListener("keydown", this._onMapKey);
      this._onMapKey = null;
    }
    if (this._mapRoute && this._map) {
      try {
        this._map.removeLayer(this._mapRoute);
      } catch (_e) {
      }
      this._mapRoute = null;
    }
    if (this._mapRouteOutline && this._map) {
      try {
        this._map.removeLayer(this._mapRouteOutline);
      } catch (_e) {
      }
      this._mapRouteOutline = null;
    }
    [this._mapBoardMarker, this._mapAlightMarker].forEach((marker) => {
      if (!marker || !this._map) return;
      try {
        this._map.removeLayer(marker);
      } catch (_e) {
      }
    });
    this._mapBoardMarker = null;
    this._mapAlightMarker = null;
    (this._mapRouteLayers || []).forEach((layer) => {
      if (!this._map || !layer) return;
      try {
        this._map.removeLayer(layer);
      } catch (_e) {
      }
    });
    this._mapRouteLayers = [];
    if (this._map) {
      try {
        this._map.remove();
      } catch (_e) {
      }
      this._map = null;
      this._mapMarker = null;
    }
    if (this._mapOverlay && this._mapOverlay.parentNode) {
      this._mapOverlay.parentNode.removeChild(this._mapOverlay);
    }
    this._mapOverlay = null;
    this._openMapKey = null;
    this._openingMap = "";
    this._mapRef = null;
    this._mapState = null;
    this._mapL = null;
    const back = this._mapReturnFocus;
    this._mapReturnFocus = null;
    if (hadOverlay) this._restoreFocus(back);
  }
  _mapStale(seq) {
    return seq !== this._mapSeq || !this.isConnected;
  }
  _mapKey(row) {
    return `${row.label || ""}|${row.headsign || ""}|${row.vehicleId || row.tripId || ""}`;
  }
  /* What the open map shows, fixed when it opens. _mapKey is not: the
     vehicle id arrives with GPS, and an ELVIRA row takes the FUTÁR trip id,
     so a poll would no longer find the row. */
  _mapRefOf(row) {
    return {
      tripId: String(row && row.tripId || ""),
      label: String(row && row.label || ""),
      headsign: String(row && row.headsign || ""),
      sched: Number(row && (row.schedTs || row.depTs) || 0)
    };
  }
  _findMapRow(ref, rows) {
    if (!ref || !rows) return null;
    const byTrip = ref.tripId && rows.find((r) => String(r.tripId || "") === ref.tripId);
    if (byTrip) return byTrip;
    if (!ref.sched) return null;
    return rows.find((r) => Number(r.schedTs || r.depTs || 0) === ref.sched && (String(r.label || "") === ref.label || String(r.headsign || "") === ref.headsign)) || null;
  }
  /* The focused element in this card's own tree (a row's map button, for a
     keyboard user), to come back to when the overlay closes. */
  _focusTarget() {
    const el = this.shadowRoot && this.shadowRoot.activeElement;
    return el ? { el, ref: null } : null;
  }
  /* Back where the overlay was opened from. A poll repaints the table, so
     the row's map button may be a new element by then: find that one. */
  _restoreFocus(back) {
    if (!back || !back.el) return;
    let el = back.el;
    if (!el.isConnected && back.ref && this._elBody) {
      const rows = this._shownRows || [];
      const idx = rows.indexOf(this._findMapRow(back.ref, rows));
      el = idx >= 0 ? this._elBody.querySelector(`tr[data-row-index="${idx}"] .map-open`) : null;
    }
    if (!el || !el.isConnected || typeof el.focus !== "function") return;
    try {
      el.focus();
    } catch (_e) {
    }
  }
  /* Stops in the style of a metro map: boarding is a dot in the line's colour,
     alighting a ring, a transfer a neutral ring; the journey's start is a
     blue dot and its end a target. Boarding names sit right of the pin with
     the line's badge, alighting names left, so a transfer between two nearby
     stops shows both. */
  _stopPinIcon(L, pin, lang, withLabel) {
    const esc = BkkLib.esc;
    const word = {
      board: "mapBoardStop",
      alight: "mapAlightStop",
      transfer: "mapTransferStop",
      start: "mapStartPoint",
      end: "mapEndPoint"
    }[pin.kind] || "mapBoardStop";
    const tip = t(lang, word) + (pin.name ? ": " + pin.name : "");
    const line = BkkLib.normMapHex(pin.color) || "#1a1a1a";
    const ink = BkkLib.normMapHex(pin.text) || BkkLib.mapTextOn(line);
    const badge = pin.label && pin.kind !== "alight" ? `<b class="ht-stop-line">${esc(pin.label)}</b>` : "";
    const label = withLabel && pin.name ? `<span class="ht-stop-lbl">${badge}<span class="ht-stop-name">${esc(pin.name)}</span></span>` : "";
    return L.divIcon({
      className: "ht-stop-pin-wrap",
      html: `<div class="ht-stop-pin ht-stop-${esc(pin.kind)}" style="--line:${esc(line)};--ink:${esc(ink)}" title="${esc(tip)}"><span class="ht-stop-mark"></span>${label}</div>`,
      iconSize: [20, 20],
      iconAnchor: [10, 10]
    });
  }
  /* The card's own map overlay lives outside the shadow root; the planner's
     inline map lives inside it. Both include these rules. */
  _stopPinCss() {
    return `
        .ht-stop-pin{position:relative;width:20px;height:20px;display:flex;align-items:center;justify-content:center}
        .ht-stop-mark{width:16px;height:16px;box-sizing:border-box;border-radius:50%;background:var(--line,#1a1a1a);border:3px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.25),0 1px 4px rgba(0,0,0,.4)}
        .ht-stop-alight .ht-stop-mark{background:#fff;border:4px solid var(--line,#1a1a1a)}
        .ht-stop-transfer .ht-stop-mark{width:18px;height:18px;background:#fff;border:4px solid #1a1a1a}
        .ht-stop-start .ht-stop-mark{width:14px;height:14px;background:#1a73e8;border:3px solid #fff;box-shadow:0 0 0 4px rgba(26,115,232,.25),0 1px 4px rgba(0,0,0,.4)}
        .ht-stop-end .ht-stop-mark{width:18px;height:18px;background:#1a1a1a;border:3px solid #fff;box-shadow:0 0 0 2px #1a1a1a,0 1px 5px rgba(0,0,0,.45)}
        .ht-stop-lbl{position:absolute;left:19px;top:50%;transform:translateY(-50%);display:flex;align-items:center;gap:5px;white-space:nowrap;max-width:220px;padding:3px 9px 3px 4px;border-radius:999px;background:#fff;color:#1f1f1f;font:600 12px/1.2 system-ui,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3);pointer-events:none}
        .ht-stop-lbl:not(:has(.ht-stop-line)){padding-left:9px}
        .ht-stop-alight .ht-stop-lbl,.ht-stop-end .ht-stop-lbl{left:auto;right:19px}
        .ht-stop-name{overflow:hidden;text-overflow:ellipsis}
        .ht-stop-line{flex:none;min-width:14px;padding:1px 6px;border-radius:999px;background:var(--line,#1a1a1a);color:var(--ink,#fff);font:800 11px/1.25 system-ui,sans-serif;text-align:center}
    `;
  }
  /* The vehicle: a larger disc with its type icon (bus, tram, …), so it is
     not mistaken for the boarding stop's dot in the same colour. */
  _dotIcon(row, live, lang, colorHex) {
    const esc = BkkLib.esc;
    const color = BkkLib.normMapHex(colorHex || row && row.color) || "#2E5EA8";
    const ink = BkkLib.normMapHex(row && row.textcolor) || BkkLib.mapTextOn(color);
    const pulse = live ? "ht-veh-dot-live" : "ht-veh-dot-est";
    const tip = live ? t(lang, "mapLiveDot") : t(lang, "mapEstDot");
    const icon = String(row && row.icon || "mdi:bus");
    return `<div class="ht-veh-dot ${pulse}" style="--bg:${esc(color)};--ink:${esc(ink)}" title="${esc(tip)}"><span class="ht-veh-dot-core"><ha-icon icon="${esc(icon)}"></ha-icon></span></div>`;
  }
  _mapChromeCss() {
    return `
        .ht-map-overlay{position:fixed;inset:0;z-index:2147483000;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}
        .ht-map-panel{width:min(720px,100%);height:min(70vh,560px);background:var(--card-background-color,#fff);border-radius:28px;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 12px 40px rgba(0,0,0,.35);color:var(--primary-text-color,#222)}
        .ht-map-head{display:flex;align-items:center;gap:10px;padding:10px 12px;border-bottom:1px solid var(--divider-color,rgba(0,0,0,.08));font:600 14px/1.3 system-ui,sans-serif}
        .ht-map-head .meta{flex:1;min-width:0}
        .ht-map-head .sub{font-weight:400;font-size:12px;opacity:.75;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .ht-map-close{border:0;background:transparent;font-size:22px;line-height:1;cursor:pointer;padding:4px 8px;opacity:.7;color:inherit}
        .ht-map-canvas{flex:1;height:420px;min-height:420px;position:relative}
        .ht-map-foot{padding:8px 12px;font:12px/1.35 system-ui,sans-serif;opacity:.85;border-top:1px solid var(--divider-color,rgba(0,0,0,.08))}
        .ht-veh-dot{width:32px;height:32px;display:flex;align-items:center;justify-content:center;filter:drop-shadow(0 1px 3px rgba(0,0,0,.45))}
        .ht-veh-dot-core{width:26px;height:26px;border-radius:50%;background:var(--bg,#2E5EA8);color:var(--ink,#fff);border:2.5px solid #fff;box-sizing:border-box;display:flex;align-items:center;justify-content:center}
        .ht-veh-dot-core ha-icon{--mdc-icon-size:15px;display:flex}
        .ht-veh-dot-live .ht-veh-dot-core{box-shadow:0 0 0 4px color-mix(in srgb, var(--bg,#2E5EA8) 35%, transparent)}
        .ht-veh-dot-est .ht-veh-dot-core{border-style:dashed;opacity:.95}
${this._stopPinCss()}
        .ht-map-canvas .leaflet-container{width:100%;height:100%;min-height:420px;overflow:hidden}
        .ht-map-canvas .leaflet-pane,.ht-map-canvas .leaflet-tile,.ht-map-canvas .leaflet-marker-icon,.ht-map-canvas .leaflet-marker-shadow,.ht-map-canvas .leaflet-tile-container,.ht-map-canvas .leaflet-zoom-box,.ht-map-canvas .leaflet-image-layer,.ht-map-canvas .leaflet-layer{position:absolute;left:0;top:0}
        .ht-map-canvas .leaflet-pane>svg,.ht-map-canvas .leaflet-pane>canvas{position:absolute}
        .ht-map-canvas .leaflet-container svg{max-width:none!important;max-height:none!important}
        .ht-map-canvas .leaflet-tile-pane{z-index:200}
        .ht-map-canvas .leaflet-overlay-pane{z-index:400}
        .ht-map-canvas .leaflet-marker-pane{z-index:600}
    `;
  }
  _mapFootText(row, hasShape, hasGps, estimated, lang, hasBoard, hasAlight) {
    const bits = [];
    if (hasShape) bits.push(t(lang, "mapFullRoute"));
    if (hasBoard) bits.push("● " + t(lang, "mapBoardStop"));
    if (hasAlight) bits.push("○ " + t(lang, "mapAlightStop"));
    if (hasGps) bits.push(t(lang, "mapLivePos"));
    else if (estimated) bits.push(t(lang, "mapEstPos"));
    else if (hasShape) bits.push(t(lang, "mapNoGps"));
    if (row.stopDistancePercent != null && row.stopDistancePercent !== "") {
      bits.push(t(lang, "mapUntilStop", row.stopDistancePercent));
    }
    if (row.vehicleStatus) bits.push(String(row.vehicleStatus));
    if (row.licensePlate) bits.push(String(row.licensePlate));
    if (row.predicted_attime && row.attime && row.predicted_attime !== row.attime) {
      bits.push(t(lang, "mapExpectedVs", [row.predicted_attime, row.attime]));
    } else if (row.predicted_attime || row.attime) {
      bits.push(t(lang, "mapExpected", row.predicted_attime || row.attime));
    }
    return bits.join(" · ") || t(lang, "mapFootFallback");
  }
  _showMapNotice(message) {
    const back = this._mapReturnFocus || this._focusTarget();
    this._closeVehicleMap();
    this._mapReturnFocus = back;
    const overlay = document.createElement("div");
    overlay.className = "ht-map-overlay";
    overlay.innerHTML = `
      <style>
        .ht-map-overlay{position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;padding:16px}
        .ht-map-notice{background:var(--card-background-color,#fff);padding:16px 18px;border-radius:10px;max-width:360px;font:14px/1.4 system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.3);color:var(--primary-text-color,#222)}
        .ht-map-notice button{margin-top:12px;border:0;background:var(--primary-color,#03a9f4);color:#fff;padding:8px 12px;border-radius:6px;cursor:pointer}
      </style>
      <div class="ht-map-notice" role="dialog" aria-modal="true">${BkkLib.esc(message)}<br><button type="button">${BkkLib.esc(t(this._lang(), "mapOk"))}</button></div>
    `;
    document.body.appendChild(overlay);
    this._mapOverlay = overlay;
    const close = () => this._closeVehicleMap();
    const ok = overlay.querySelector("button");
    ok.addEventListener("click", close);
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) close();
    });
    this._onMapKey = (ev) => {
      if (ev.key === "Escape") close();
    };
    document.addEventListener("keydown", this._onMapKey);
    try {
      ok.focus();
    } catch (_e) {
    }
  }
  async _loadRowMapShape(row) {
    const cfg = this._config || {};
    let shape = BkkLib.validMapShape(row && row.shape);
    let board = BkkLib.mapGpsLatLon(cfg.stopLat, cfg.stopLon) || BkkLib.knownStopCoords({ id: cfg.stopId, name: cfg.stopName });
    if (BkkLib.isTrainRow(row)) {
      try {
        const mav = await BkkLib.withIndexes(cfg).mavRouteForRow(row, cfg.stopName || "", cfg.destName || cfg.destKey || "");
        if (mav && mav.shape.length >= 2) {
          const ownBoard = mav.board || board;
          const ownAlight = mav.alight || await this._rowAlightPoint(row, []);
          return {
            shape: mav.shape,
            board: BkkLib.pinOnRoute(ownBoard, [], mav.shape),
            alight: ownAlight && BkkLib.pinOnRoute(ownAlight, [], mav.shape)
          };
        }
      } catch (_e) {
      }
    }
    const gtfsGeo = await BkkLib.gtfsRowShape(row);
    if (gtfsGeo && gtfsGeo.board) board = gtfsGeo.board;
    if (gtfsGeo && gtfsGeo.shape.length >= 2) {
      return { shape: gtfsGeo.shape, board, alight: await this._rowAlightPoint(row, [gtfsGeo.dest]) };
    }
    const tripId = row && row.tripId || "";
    const canFutar = this._hasFutar() && (!/^motis:/i.test(String(tripId || "")) || !!BkkLib.rowTrainNumber(row));
    let geomTrip = "";
    if (canFutar) {
      geomTrip = BkkLib.isFutarTripId(tripId) ? tripId : "";
      if (!geomTrip && this._hass) {
        const hit = BkkLib.matchHassVehicle(BkkLib.collectHassVehicles(this._hass), row);
        if (hit && BkkLib.isFutarTripId(hit.tripId)) geomTrip = hit.tripId;
      }
    }
    let futarShape = [];
    if (geomTrip && this._hasFutar() && !BkkLib.isTrainRow(row)) {
      try {
        futarShape = await BkkLib.futarTripShape(this._apiKey(), this._tripCache || {}, geomTrip);
      } catch (_e) {
      }
    }
    let futarHop = null;
    if (geomTrip && this._hasFutar() && cfg.destKey) {
      try {
        futarHop = await BkkLib.tripHopStops(
          this._apiKey(),
          this._tripCache || {},
          geomTrip,
          { id: cfg.stopId, name: cfg.stopName },
          cfg.destKey
        );
      } catch (_e) {
      }
    }
    const published = BkkLib.keepsFullLineShape(row) && !BkkLib.isTrainRow(row) && futarShape.length >= 8;
    let motisShape = [];
    let motisCoversHead = true;
    let motisMap = null;
    let motisBoardPt = null;
    const area = await BkkLib.withIndexes(cfg).stopArea(cfg);
    if (!published) {
      try {
        motisMap = await BkkLib.motisShapeForHop(
          row,
          {
            id: cfg.stopId,
            name: cfg.stopName,
            lat: cfg.stopLat != null ? cfg.stopLat : gtfsGeo && gtfsGeo.board && gtfsGeo.board[0],
            lon: cfg.stopLon != null ? cfg.stopLon : gtfsGeo && gtfsGeo.board && gtfsGeo.board[1],
            area
          },
          {
            id: cfg.destStopId,
            name: cfg.destName,
            key: cfg.destKey,
            lat: gtfsGeo && gtfsGeo.dest ? gtfsGeo.dest[0] : void 0,
            lon: gtfsGeo && gtfsGeo.dest ? gtfsGeo.dest[1] : void 0,
            area
          },
          { minutesAfter: cfg.minutesAfter, mode: "all" }
        );
        motisShape = BkkLib.validMapShape(motisMap && motisMap.shape);
        motisCoversHead = !!(motisMap && motisMap.coversHead);
        if (motisMap && motisMap.board) {
          const motisBoard = BkkLib.mapGpsLatLon(motisMap.board[0], motisMap.board[1]);
          motisBoardPt = motisBoard;
          if (motisBoard) {
            const orig = board;
            if (!orig || BkkLib.haversineMeters(orig, motisBoard) <= 300) {
              board = motisBoard;
            }
          }
        }
      } catch (_e) {
      }
    }
    if (futarHop && futarHop.board && (!board || BkkLib.haversineMeters(board, futarHop.board) <= 300)) {
      board = futarHop.board;
    }
    if (!board) {
      board = BkkLib.knownStopCoords({ id: cfg.stopId, name: cfg.stopName });
    }
    if (!published && shape.length < 2 && /^gtfs:/i.test(String(row && row.tripId || "")) && this._hass) {
      try {
        const points = await BkkLib.coachShape(this._hass, row.label, row.headsign || "");
        shape = BkkLib.validMapShape(BkkLib.decodePolyline(points));
      } catch (_e) {
      }
    }
    if (canFutar && BkkLib.isTrainRow(row) && BkkLib.isFutarTripId(geomTrip)) {
      if (futarShape.length < 2 && motisShape.length < 2 && this._hasFutar()) {
        try {
          futarShape = await BkkLib.futarTripShape(this._apiKey(), this._tripCache || {}, geomTrip);
        } catch (_e) {
        }
      }
    }
    const clipForeignFutar = (pts) => {
      const valid = BkkLib.validMapShape(pts);
      if (BkkLib.keepsFullLineShape(row)) return valid;
      if (!board || valid.length < 2) return valid;
      const startGap = BkkLib.haversineMeters(valid[0], board);
      if (startGap <= 2500) return valid;
      return BkkLib.clipRideShape(
        valid,
        board,
        BkkLib.mapGpsLatLon(cfg.destLat, cfg.destLon) || BkkLib.knownStopCoords({
          id: cfg.destStopId,
          name: cfg.destName,
          key: cfg.destKey
        }),
        row
      );
    };
    if (!BkkLib.keepsFullLineShape(row)) {
      futarShape = clipForeignFutar(futarShape);
    }
    if (published) {
      shape = BkkLib.pickPublishedLineShape(motisShape, futarShape, row, motisCoversHead);
    } else {
      shape = BkkLib.combineHopShapes(
        motisShape,
        futarShape,
        motisCoversHead,
        /* The row's own line, or the coach_shape service's for a GTFS row. */
        BkkLib.isTrainRow(row) ? clipForeignFutar(shape) : shape
      );
      const motisGap = BkkLib.shapeGpsGapMeters(motisShape, row && row.lat, row && row.lon);
      const futarGap = BkkLib.shapeGpsGapMeters(futarShape, row && row.lat, row && row.lon);
      if (motisGap != null && futarGap != null && futarGap + 400 < motisGap && futarGap < 3e3) {
        shape = futarShape;
      } else if (motisGap != null && motisGap > 4e3 && futarShape.length >= 2 && (futarGap == null || futarGap < motisGap)) {
        shape = futarShape;
      }
    }
    const destPt = await this._rowAlightPoint(row, [futarHop && futarHop.alight, motisMap && motisMap.dest]);
    const clipped = BkkLib.clipRideShape(shape, board, destPt, row);
    if (clipped.length >= 2) shape = clipped;
    shape = BkkLib.validMapShape(shape);
    if (shape.length < 2 && gtfsGeo && gtfsGeo.stopLine.length >= 2) shape = gtfsGeo.stopLine;
    let approx = false;
    if (shape.length < 2 && board && destPt && BkkLib.haversineMeters(board, destPt) > 30) {
      shape = [board, destPt];
      approx = true;
    }
    return {
      shape,
      approx,
      board: BkkLib.pinOnRoute(board, [futarHop && futarHop.board, motisBoardPt], shape),
      alight: destPt && BkkLib.pinOnRoute(destPt, [futarHop && futarHop.alight, motisMap && motisMap.dest], shape)
    };
  }
  /* Where the rider gets off: the trip's own stop (GTFS or FUTÁR) first, then
     Transitous's hop end, the configured stop, and a Transitous lookup.
     Cards without a destination have no alighting stop. */
  async _rowAlightPoint(row, candidates) {
    const cfg = this._config || {};
    if (!cfg.destKey && !cfg.destName && !cfg.destStopId) return null;
    const asPt = (p) => Array.isArray(p) ? BkkLib.mapGpsLatLon(p[0], p[1]) : null;
    const known = (candidates || []).map(asPt).find(Boolean) || BkkLib.mapGpsLatLon(cfg.destLat, cfg.destLon) || BkkLib.knownStopCoords({ id: cfg.destStopId, name: cfg.destName, key: cfg.destKey });
    if (known || !cfg.destName) return known || null;
    try {
      const ref = await BkkLib.transitousStopRef({
        id: cfg.destStopId,
        name: cfg.destName,
        key: cfg.destKey,
        lat: cfg.destLat,
        lon: cfg.destLon,
        area: await BkkLib.withIndexes(cfg).stopArea(cfg)
      }, BkkLib.vehicleBucket(row));
      return ref && BkkLib.mapGpsLatLon(ref.lat, ref.lon) || null;
    } catch (_e) {
      return null;
    }
  }
  async _openVehicleMap(row) {
    const lang = this._lang();
    const cfg = this._config || {};
    const key = this._mapKey(row);
    if (this._openingMap === key) return;
    if (this._mapOverlay && this._openMapKey === key) return;
    const back = this._mapReturnFocus || this._focusTarget();
    this._closeVehicleMap();
    if (back && !back.ref) back.ref = this._mapRefOf(row);
    this._mapReturnFocus = back;
    this._openingMap = key;
    const seq = this._mapSeq;
    const gps0 = BkkLib.mapGpsLatLon(row.lat, row.lon);
    let lat = gps0 ? gps0[0] : NaN;
    let lon = gps0 ? gps0[1] : NaN;
    let hasGps = !!gps0;
    let shape = BkkLib.validMapShape(row.shape);
    let hasShape = shape.length >= 2;
    let positionEstimated = false;
    const tripId = row.tripId || "";
    if (!hasGps && !tripId && !hasShape) {
      this._showMapNotice(t(lang, "mapNoData"));
      return;
    }
    let L;
    try {
      L = await this._ensureLeaflet();
    } catch (_e) {
      if (!this._mapStale(seq)) this._showMapNotice(t(lang, "mapLeafletFail"));
      return;
    }
    if (this._mapStale(seq)) return;
    const overlay = document.createElement("div");
    overlay.className = "ht-map-overlay";
    const sub = this._mapTypeLabel(row.vehicle || row.type, lang) + (row.model ? " · " + row.model : "");
    overlay.innerHTML = `
      <style>${this._mapChromeCss()}</style>
      <div class="ht-map-panel" role="dialog" aria-modal="true">
        <div class="ht-map-head">
          <div class="meta">
            <div>${BkkLib.esc(this._mapGlyph(row.vehicle || row.type))} ${BkkLib.esc(row.label || "")} → ${BkkLib.esc(row.headsign || "")}</div>
            <div class="sub">${BkkLib.esc(sub)}</div>
          </div>
          <button type="button" class="ht-map-close" aria-label="${BkkLib.esc(t(lang, "mapClose"))}">×</button>
        </div>
        <div class="ht-map-canvas"></div>
        <div class="ht-map-foot">${BkkLib.esc(t(lang, "mapLoadingRoute"))}</div>
      </div>
    `;
    (document.documentElement || document.body).appendChild(overlay);
    this._mapOverlay = overlay;
    this._openMapKey = key;
    this._mapRef = this._mapRefOf(row);
    this._mapL = L;
    const st = {
      ready: false,
      hasShape: false,
      approx: false,
      hasGps,
      estimated: false,
      hasBoard: false,
      hasAlight: false,
      hasPosition: hasGps
    };
    this._mapState = st;
    this._onMapKey = (ev) => {
      if (ev.key === "Escape") this._closeVehicleMap();
    };
    document.addEventListener("keydown", this._onMapKey);
    const closeBtn = overlay.querySelector(".ht-map-close");
    closeBtn.addEventListener("click", () => this._closeVehicleMap());
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) this._closeVehicleMap();
    });
    try {
      closeBtn.focus();
    } catch (_e) {
    }
    const canvas = overlay.querySelector(".ht-map-canvas");
    const foot = overlay.querySelector(".ht-map-foot");
    const startLat = hasGps ? lat : 47.5;
    const startLon = hasGps ? lon : 19.05;
    let map;
    try {
      map = L.map(canvas, { scrollWheelZoom: true, preferCanvas: false }).setView([startLat, startLon], hasGps ? 12 : 8);
      L.tileLayer("https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png", {
        maxZoom: 20,
        subdomains: "abc",
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://www.openstreetmap.fr/">OpenStreetMap France</a>'
      }).addTo(map);
    } catch (_e) {
      this._showMapNotice(t(lang, "mapLeafletFail"));
      return;
    }
    this._map = map;
    this._mapRouteLayers = [];
    const resizeMap = () => {
      if (this._map !== map) return;
      try {
        map.invalidateSize();
      } catch (_e) {
      }
    };
    requestAnimationFrame(() => {
      resizeMap();
      requestAnimationFrame(resizeMap);
    });
    const drawMarker = () => {
      if (!this._map || this._map !== map) return;
      const here = BkkLib.mapGpsLatLon(lat, lon);
      if (!here) return;
      if (this._mapMarker) {
        try {
          this._map.removeLayer(this._mapMarker);
        } catch (_e) {
        }
      }
      const icon = L.divIcon({
        className: "",
        html: this._dotIcon(row, hasGps && !positionEstimated, lang),
        iconSize: [32, 32],
        iconAnchor: [16, 16]
      });
      this._mapMarker = L.marker(here, { icon, zIndexOffset: 600 }).addTo(map);
    };
    if (hasGps) {
      drawMarker();
      foot.textContent = this._mapFootText(row, false, true, false, lang);
    }
    let board = BkkLib.mapGpsLatLon(cfg.stopLat, cfg.stopLon) || BkkLib.knownStopCoords({ id: cfg.stopId, name: cfg.stopName });
    let alight = null;
    let approx = false;
    try {
      const got = await this._loadRowMapShape(row);
      shape = BkkLib.validMapShape(got.shape);
      hasShape = shape.length >= 2;
      approx = hasShape && !!got.approx;
      if (got.board) board = got.board;
      alight = got.alight || null;
    } catch (_e) {
    }
    if (alight && board && BkkLib.haversineMeters(board, alight) < 30) alight = null;
    if (this._mapStale(seq) || !this._map || this._map !== map || !this._mapOverlay) return;
    if (st.hasGps && this._mapMarker && typeof this._mapMarker.getLatLng === "function") {
      const at = this._mapMarker.getLatLng();
      const got = at && BkkLib.mapGpsLatLon(at.lat, at.lng);
      if (got) {
        lat = got[0];
        lon = got[1];
        hasGps = true;
      }
    }
    const pos = BkkLib.mapGpsLatLon(lat, lon);
    const hasPosition = !!pos;
    if (hasPosition) {
      lat = pos[0];
      lon = pos[1];
    }
    const routeColor = BkkLib.mapRouteColor(row, {});
    const lineBase = {
      lineJoin: "round",
      lineCap: "round",
      noClip: true,
      pane: "overlayPane"
    };
    const applyView = () => {
      try {
        map.invalidateSize();
      } catch (_e) {
      }
      const points = [board, alight, hasPosition ? [lat, lon] : null].filter(Boolean);
      if (hasShape) {
        const bounds = L.latLngBounds(shape);
        points.forEach((pt) => bounds.extend(pt));
        map.fitBounds(bounds, { padding: [36, 36], maxZoom: 12, animate: false });
      } else if (points.length >= 2) {
        map.fitBounds(L.latLngBounds(points), { padding: [36, 36], maxZoom: 12, animate: false });
      } else if (points.length) {
        map.setView(points[0], 13, { animate: false });
      }
      (this._mapRouteLayers || []).forEach((layer) => {
        if (layer && typeof layer.redraw === "function") {
          try {
            layer.redraw();
          } catch (_e) {
          }
        }
      });
    };
    if (hasShape) {
      const dash = approx ? { dashArray: "10 10" } : {};
      const outline = L.polyline(shape, Object.assign({
        color: "#1a1a1a",
        weight: 7,
        opacity: 0.45,
        interactive: false
      }, lineBase, dash)).addTo(map);
      const line2 = L.polyline(shape, Object.assign({
        color: routeColor,
        weight: 5,
        opacity: 0.98
      }, lineBase, dash)).addTo(map);
      this._mapRouteLayers.push(outline, line2);
      this._mapRouteOutline = outline;
      this._mapRoute = line2;
    }
    const stopPin = (pin) => L.marker(pin.pt, {
      icon: this._stopPinIcon(L, pin, lang, true),
      zIndexOffset: 500,
      keyboard: false
    }).addTo(map);
    const line = { color: routeColor, label: row.label || "", text: row.textcolor || "" };
    if (alight) {
      this._mapAlightMarker = stopPin({ kind: "alight", pt: alight, name: cfg.destName || "", color: routeColor });
    }
    if (board) this._mapBoardMarker = stopPin(Object.assign({ kind: "board", pt: board, name: cfg.stopName || "" }, line));
    if (hasPosition && !this._mapMarker) drawMarker();
    applyView();
    Object.assign(st, {
      ready: true,
      hasShape,
      approx,
      hasGps,
      estimated: positionEstimated,
      hasBoard: !!board,
      hasAlight: !!alight,
      hasPosition
    });
    foot.textContent = this._mapFoot(row, st, lang);
    setTimeout(() => {
      if (this._map === map) applyView();
    }, 80);
    this._openingMap = "";
  }
  _mapFoot(row, st, lang) {
    if (!st.hasShape && !st.hasBoard && !st.hasPosition) return t(lang, "mapNothingToShow");
    if (!st.hasShape) return t(lang, "mapNoGeometry");
    const text = this._mapFootText(row, st.hasShape, st.hasGps, st.estimated, lang, st.hasBoard, st.hasAlight);
    return st.approx ? t(lang, "mapApproxLine") + " · " + text : text;
  }
  /* A poll moves the vehicle on the open map, and puts it there once GPS
     shows up for a row that had none when the map opened. */
  _syncOpenMap() {
    const st = this._mapState;
    if (!this._map || !this._openMapKey || !st) return;
    const row = this._findMapRow(this._mapRef, this._rows || []);
    if (!row || !row.hasGps) return;
    const gps = BkkLib.mapGpsLatLon(row.lat, row.lon);
    if (!gps) return;
    const lang = this._lang();
    const L = this._mapL || (typeof window !== "undefined" ? window.L : null);
    const icon = L && typeof L.divIcon === "function" ? L.divIcon({
      className: "",
      html: this._dotIcon(row, true, lang),
      iconSize: [32, 32],
      iconAnchor: [16, 16]
    }) : null;
    if (this._mapMarker) {
      this._mapMarker.setLatLng(gps);
      if (icon) this._mapMarker.setIcon(icon);
    } else if (icon && typeof L.marker === "function") {
      try {
        this._mapMarker = L.marker(gps, { icon, zIndexOffset: 600 }).addTo(this._map);
      } catch (_e) {
        return;
      }
    } else {
      return;
    }
    st.hasGps = true;
    st.hasPosition = true;
    st.estimated = false;
    if (!st.ready) return;
    const foot = this._mapOverlay && this._mapOverlay.querySelector(".ht-map-foot");
    if (foot) foot.textContent = this._mapFoot(row, st, lang);
  }
  _departMode() {
    return BkkLib.mode(this._config || {});
  }
  async _reload() {
    const gen = ++this._gen;
    this._reloadActive = true;
    const cfg = this._config || {};
    if (this._poll) {
      clearInterval(this._poll);
      this._poll = null;
    }
    this._pollRun = null;
    const mode = this._departMode();
    const ready = cfg.stopId && cfg.destKey && (this._hasFutar() || mode === "volan" || mode === "helyi" || mode === "all");
    if (!ready) {
      this._rows = [];
      this._loading = false;
      if (cfg.stopId && cfg.destKey) this._err = codedError("errNoApiKey");
      this._paint();
      if (gen === this._gen) this._reloadActive = false;
      return;
    }
    const dest = {
      key: cfg.destKey,
      name: cfg.destName,
      id: cfg.destStopId || "",
      lat: cfg.destLat,
      lon: cfg.destLon
    };
    const origin = {
      id: cfg.stopId,
      name: cfg.stopName || "",
      lat: cfg.stopLat,
      lon: cfg.stopLon
    };
    const carryOver = (list) => {
      const prev = /* @__PURE__ */ new Map();
      (this._rawRows || []).forEach((row) => {
        if (row && row.tripId) prev.set(String(row.tripId), row);
      });
      if (!prev.size) return;
      list.forEach((row) => {
        const old = row && row.tripId ? prev.get(String(row.tripId)) : null;
        if (!old || old === row) return;
        if (row.travel == null && old.travel != null) row.travel = old.travel;
        if (/^(gtfs:|motis:)/.test(String(row.tripId)) && !BkkLib.mapGpsLatLon(row.lat, row.lon)) {
          const gps = BkkLib.mapGpsLatLon(old.lat, old.lon);
          if (gps) {
            row.lat = gps[0];
            row.lon = gps[1];
            row.gpsCarried = true;
          }
        }
      });
    };
    const paintRows = (list) => {
      if (gen !== this._gen) return;
      const merged = list || [];
      merged.forEach((row) => BkkLib.applyRailBadge(row));
      carryOver(merged);
      this._rawRows = merged;
      BkkLib.enrichFromHass(this._hass, this._rawRows);
      this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
      this._rowsFilled = false;
      this._err = "";
      this._loading = false;
      this._paint();
    };
    const run = async () => {
      if (gen !== this._gen) return;
      const lib = BkkLib.withIndexes(cfg);
      try {
        if (BkkLib.isPlaceStop({ id: cfg.stopId, kind: cfg.stopKind })) {
          this._rows = [];
          this._rawRows = [];
          this._loading = false;
          this._err = "";
          this._journey = null;
          this._journeyKey = "";
          this._journeyLoading = false;
          this._paint();
          if (typeof this._considerJourney === "function") this._considerJourney();
          return;
        }
        const wantElvira = (mode === "mav" || mode === "all") && BkkLib.hasElvira(this._hass);
        const elviraP = wantElvira ? lib.elviraBetween(
          this._hass,
          cfg.stopId,
          cfg.destStopId || "",
          /* The service takes 15..480 and rejects the call otherwise. */
          clampMinutesAfter(cfg.minutesAfter),
          cfg.destName || "",
          { apiKey: this._apiKey(), originName: cfg.stopName || "" }
        ).catch(() => []) : Promise.resolve([]);
        const cap = maxDepartureRows(cfg.minutesAfter);
        let earlyRows = [];
        let futarFull = null;
        let elviraRows = [];
        const publish = () => {
          if (gen !== this._gen) return;
          let list = futarFull ? BkkLib.mergeVolanRows(futarFull, earlyRows, cap) : earlyRows.slice();
          list = BkkLib.mergeVolanRows(list, elviraRows, cap, { overlayOnly: true });
          if (!list.length && startThrough && !futarFull) {
            if (this._err) {
              this._err = "";
              if (this._rowsFilled) {
                this._rows = [];
                this._rawRows = [];
                this._rowsFilled = false;
                this._loading = true;
              }
              this._paint();
            }
            return;
          }
          paintRows(list);
        };
        let startThrough = null;
        let throughResolve = () => {
        };
        const throughWait = new Promise((resolve) => {
          throughResolve = resolve;
        });
        const rows = await lib.departures(
          this._apiKey(),
          cfg.stopId,
          cfg.routeIds,
          dest,
          {
            originName: cfg.stopName || "",
            cache: this._tripCache || {},
            mode: this._departMode(),
            city: cfg.city || "",
            minutesAfter: cfg.minutesAfter,
            deferTravel: true,
            deferGps: true,
            hass: this._hass,
            skipElvira: true,
            /* The GTFS rows come before FUTÁR answers: the last run's FUTÁR
               rows stay beside them until then, instead of vanishing. */
            onPartial: (list) => {
              if (gen !== this._gen) return;
              const kept = (this._rowsFilled ? [] : this._rawRows || []).filter((row) => BkkLib.isFutarTripId(row && row.tripId)).map((row) => Object.assign({}, row));
              paintRows(kept.length ? BkkLib.mergeVolanRows(list, kept, cap) : list);
            },
            armThroughCheck: (fn) => {
              startThrough = fn;
            },
            onFutarUpdate: (full) => {
              throughResolve();
              if (gen !== this._gen) return;
              futarFull = full || [];
              publish();
            }
          }
        );
        if (gen !== this._gen) return;
        this._journey = null;
        this._journeyKey = "";
        this._journeyLoading = false;
        earlyRows = rows || [];
        publish();
        this._syncOpenMap();
        const firstRows = this._rawRows;
        const needsTravel = (row) => row.travel == null && !/^(gtfs:|elvira:|motis:)/.test(String(row.tripId || ""));
        const fillTravel = (list) => Promise.all((list || []).map(async (row) => {
          if (!needsTravel(row)) return;
          row.travel = await BkkLib.travelMin(
            this._apiKey(),
            row.tripId,
            cfg.destKey,
            row.dep,
            origin,
            this._tripCache || {}
          );
        }));
        const gpsTravel = Promise.all([
          BkkLib.attachCoachGps(this._hass, firstRows).then((withGps) => {
            if (gen !== this._gen || this._rawRows !== firstRows) return;
            this._rawRows = withGps;
            this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
            this._paint();
            this._syncOpenMap();
          }),
          fillTravel(firstRows)
        ]);
        if (startThrough) {
          startThrough();
        } else {
          throughResolve();
        }
        elviraRows = await elviraP;
        if (gen !== this._gen) return;
        publish();
        await throughWait;
        if (gen !== this._gen) return;
        let merged = futarFull ? BkkLib.mergeVolanRows(futarFull, earlyRows, cap) : earlyRows.slice();
        merged = BkkLib.mergeVolanRows(merged, elviraRows, cap, { overlayOnly: true });
        if (!merged.length && mode !== "helyi") {
          merged = await BkkLib.fillTransitousIfEmpty(merged, origin, dest, {
            mode: this._departMode(),
            minutesAfter: cfg.minutesAfter
          });
          if (gen !== this._gen) return;
          merged = BkkLib.mergeVolanRows(merged, elviraRows, cap, { overlayOnly: true });
          earlyRows = merged;
          paintRows(merged);
        }
        if (typeof this._considerJourney === "function") this._considerJourney();
        await gpsTravel;
        if (gen !== this._gen) return;
        if (this._rawRows !== firstRows) {
          const latest = this._rawRows;
          const [withGps] = await Promise.all([
            BkkLib.attachCoachGps(this._hass, latest),
            fillTravel(latest)
          ]);
          if (gen !== this._gen || this._rawRows !== latest) return;
          this._rawRows = withGps;
        }
        this._rows = this._rawRows.map((r) => BkkLib.asRow(r, cfg.destName, this._lang()));
        this._paint();
        this._syncOpenMap();
      } catch (err) {
        if (gen !== this._gen) return;
        this._loading = false;
        const keepRows = (this._rawRows || []).length && !this._rowsFilled;
        if ((mode === "bkk" || mode === "mav") && !keepRows) {
          let filled = [];
          try {
            filled = await BkkLib.fillTransitousIfEmpty([], origin, dest, {
              mode,
              minutesAfter: cfg.minutesAfter
            });
          } catch (_e) {
            filled = [];
          }
          if (gen !== this._gen) return;
          if (filled.length) {
            paintRows(filled);
            this._rowsFilled = true;
          }
        }
        this._showErr(err);
      }
    };
    this._loading = !(this._rows || []).length;
    this._paint();
    try {
      await run();
      if (!this.isConnected || gen !== this._gen || !this._shouldPoll()) return;
      this._pollRun = { gen, run, busy: false };
      if (this._pageHidden()) return;
      this._armPoll(this._pollRun);
    } finally {
      if (gen === this._gen) this._reloadActive = false;
    }
  }
};

// src/planner-card.js
var PLANNER_STALE_MS = 5 * 60 * 1e3;
var HISTORY_RETRY_MS = 60 * 1e3;
var MAP_TILES = "https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png";
var MAP_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://www.openstreetmap.fr/">OpenStreetMap France</a>';
var BKKPlannerCard = class extends BKKHopCard {
  constructor() {
    super();
    this._searchTimer = null;
    this._destTimer = null;
    this._searchSeq = 0;
    this._destSeq = 0;
    this._destSearchSeq = 0;
    this._dests = [];
    this._destRoutes = {};
    this._destHits = [];
    this._destsLoading = false;
    this._destsFor = "";
    this._pickersMounted = false;
    this._when = { mode: "now", at: 0 };
    this._journeys = [];
    this._openJourney = 0;
  }
  static async getConfigElement() {
    if (!customElements.get(BKK_PLANNER_EDITOR)) {
      await customElements.whenDefined(BKK_PLANNER_EDITOR);
    }
    return document.createElement(BKK_PLANNER_EDITOR);
  }
  static getStubConfig() {
    return {
      name: "",
      language: "auto",
      minutesAfter: DEFAULT_MINUTES_AFTER,
      stopId: "",
      stopName: "",
      destKey: "",
      destName: "",
      destStopId: "",
      routeIds: []
    };
  }
  getCardSize() {
    return Math.max(6, super.getCardSize() + 3);
  }
  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto", min_rows: 6 };
  }
  _departMode() {
    return "all";
  }
  _shouldPoll() {
    return false;
  }
  /* Back on the view after a while, the departures and countdowns on screen
     are stale: plan again. A chosen time does not go stale, nor do its
     itineraries when all are folded or none was found. */
  _shouldReloadOnConnect() {
    const planned = this._timed() && (this._journeys && this._journeys.length || this._journeysDone);
    const idle = !(planned || this._journey || this._journeyLoading || this._rows && this._rows.length);
    const stale = !this._timed() && this._loadedAt > 0 && Date.now() - this._loadedAt > PLANNER_STALE_MS;
    return idle || stale;
  }
  /* A new origin, destination or a swap: whatever the running reload or
     journey lookup still fetches belongs to the old pair and must not paint. */
  _invalidatePlan() {
    this._gen += 1;
    if (this._searchTimer) {
      clearTimeout(this._searchTimer);
      this._searchTimer = null;
    }
    if (this._destTimer) {
      clearTimeout(this._destTimer);
      this._destTimer = null;
    }
    this._searchSeq += 1;
    this._destSearchSeq += 1;
    this._reloadActive = false;
    if (this._poll) {
      clearInterval(this._poll);
      this._poll = null;
    }
    this._rows = [];
    this._rawRows = [];
    this._err = "";
    this._journey = null;
    this._journeys = [];
    this._journeyKey = "";
    this._journeyLoading = false;
    this._journeysDone = false;
    this._openJourney = 0;
  }
  _timed() {
    return this._when && this._when.mode !== "now" && this._when.at > 0;
  }
  /* A chosen time plans itineraries for that moment. The live departure
     board stays for "now": FUTÁR, the GTFS indexes and ELVIRA all answer
     for the present. */
  async _reload() {
    this._loadedAt = Date.now();
    if (!this._timed()) return super._reload();
    const gen = ++this._gen;
    this._reloadActive = true;
    if (this._poll) {
      clearInterval(this._poll);
      this._poll = null;
    }
    this._rows = [];
    this._rawRows = [];
    this._err = "";
    this._loading = false;
    this._journeyKey = "";
    this._paint();
    this._considerJourney();
    if (gen === this._gen) this._reloadActive = false;
  }
  _considerJourney() {
    const cfg = this._config || {};
    const horizon = clampMinutesAfter(cfg.minutesAfter);
    const timed = this._timed();
    const when = timed ? `${this._when.mode}@${this._when.at}` : "now";
    const key = [cfg.stopId, cfg.destStopId || "", cfg.destName, horizon, when].join("|");
    if (!cfg.stopId || !cfg.destName) return;
    if (this._journeyKey === key && (this._journeyLoading || this._journey || timed && this._journeysDone)) return;
    const gen = this._gen;
    this._journeyKey = key;
    this._journeyLoading = true;
    this._journeysDone = false;
    const origin = { id: cfg.stopId, name: cfg.stopName || "", lat: cfg.stopLat, lon: cfg.stopLon };
    const dest = { id: cfg.destStopId || "", name: cfg.destName || "", lat: cfg.destLat, lon: cfg.destLon };
    if (timed) {
      this._journeys = [];
      this._journey = null;
      this._openJourney = 0;
      this._paintJourney();
      BkkLib.withIndexes(this._config).planJourneys(this._apiKey(), origin, dest, this._hass, {
        time: this._when.at,
        arriveBy: this._when.mode === "arrive",
        minutesAfter: 120
      }).then((list) => {
        if (gen !== this._gen) return;
        this._journeyLoading = false;
        this._journeysDone = true;
        this._journeys = list || [];
        this._openJourney = 0;
        this._journey = this._journeys[0] || null;
        this._paint();
      }).catch(() => {
        if (gen !== this._gen) return;
        this._journeyLoading = false;
        this._journeysDone = true;
        this._journeys = [];
        this._paint();
      });
      return;
    }
    BkkLib.withIndexes(this._config).planJourney(
      this._apiKey(),
      origin,
      dest,
      this._hass,
      { minutesAfter: horizon }
    ).then(async (journey) => {
      if (gen !== this._gen) return;
      this._journeyLoading = false;
      this._journey = journey;
      this._paint();
      if (!journey) return;
      try {
        await BkkLib.fillJourneyShapes(journey);
      } catch (_e) {
      }
      if (gen !== this._gen) return;
      this._journey = journey;
      this._paintJourney();
    }).catch(() => {
      if (gen !== this._gen) return;
      this._journeyLoading = false;
    });
  }
  set hass(hass) {
    super.hass = hass;
    if (!this._pickersMounted) return;
    if (this._historyDue(hass && hass.connection || null)) this._paintFav();
    this._ensureDests();
  }
  /* Language from hass after the pickers were drawn: their labels, the
     time switch and the hints under the fields follow, not only the board. */
  _langChanged() {
    if (this._pickersMounted) this._syncPickerLabels();
    super._langChanged();
  }
  /* A hint under a search field keeps its i18n key, so a language change
     rewrites the same hint (a typed query's "no results" stays that). */
  _hintText(key) {
    const lang = this._lang();
    return key.indexOf("mode.") === 0 ? t(lang, "mode").all[key.slice(5)] : t(lang, key);
  }
  _hint(box, key) {
    box.innerHTML = `<span class="hint" data-hint="${BkkLib.esc(key)}">${BkkLib.esc(this._hintText(key))}</span>`;
  }
  _repaintHints() {
    ["phits", "pdhits"].forEach((id) => {
      const box = this._planEl(id);
      const span = box && !box.querySelector(".hit") ? box.querySelector(".hint[data-hint]") : null;
      if (span) span.textContent = this._hintText(span.getAttribute("data-hint"));
    });
  }
  _emptyBodyMessage(lang, cfg) {
    if (this._timed()) return "";
    if (!cfg.stopId || !cfg.destKey) return "";
    if (this._journey && this._journey.legs && this._journey.legs.length) return "";
    if (this._journeyLoading) return t(lang, "cardLoading");
    return super._emptyBodyMessage(lang, cfg);
  }
  disconnectedCallback() {
    if (this._searchTimer) {
      clearTimeout(this._searchTimer);
      this._searchTimer = null;
    }
    if (this._destTimer) {
      clearTimeout(this._destTimer);
      this._destTimer = null;
    }
    this._teardownPlannerInlineMap();
    super.disconnectedCallback();
  }
  connectedCallback() {
    super.connectedCallback();
    if (!this._painted || this._plannerMap) return;
    const host = this._journeyHost && this._journeyHost.querySelector(".journey-plan");
    if (host && host.querySelector("#pjmap")) this._mountPlannerInlineMap(host);
  }
  _teardownPlannerInlineMap() {
    const map = this._plannerMap;
    this._plannerMap = null;
    if (map) {
      try {
        map.remove();
      } catch (_e) {
      }
    }
  }
  _drawJourneyPolylines(L, map, segs, opts) {
    if (!Array.isArray(segs) || !segs.length) return [];
    opts = opts || {};
    const clickable = !!opts.interactive;
    const allPts = [];
    const lineBase = { lineJoin: "round", lineCap: "round", noClip: true };
    segs.forEach((seg) => {
      const shape = seg.shape;
      shape.forEach((pt) => allPts.push(pt));
      if (seg.walk) {
        L.polyline(shape, Object.assign({
          color: "#1a1a1a",
          weight: 5,
          opacity: 0.35,
          dashArray: "8 8",
          interactive: false
        }, lineBase)).addTo(map);
        L.polyline(shape, Object.assign({
          color: "#888888",
          weight: 3,
          opacity: 0.95,
          dashArray: "8 8",
          interactive: clickable
        }, lineBase)).addTo(map);
      } else {
        L.polyline(shape, Object.assign({
          color: "#1a1a1a",
          weight: 7,
          opacity: 0.45,
          interactive: false
        }, lineBase)).addTo(map);
        L.polyline(shape, Object.assign({
          color: seg.color,
          weight: 5,
          opacity: 0.98,
          interactive: clickable
        }, lineBase)).addTo(map);
      }
    });
    const lang = this._lang();
    BkkLib.journeyStopPins(segs).forEach((pin) => {
      L.marker(pin.pt, {
        icon: this._stopPinIcon(L, pin, lang, !!opts.labels),
        zIndexOffset: pin.kind === "alight" ? 500 : 520,
        keyboard: false,
        interactive: clickable || !!opts.labels
      }).addTo(map);
    });
    return allPts;
  }
  /* Refits once the layout settles. No animation: a zoom animation ends on
     a 250 ms timer of Leaflet's own that throws once the map is removed (an
     itinerary folded or swapped right away), and remove() cannot cancel it.
     A refit due after that skips the removed map. */
  _fitJourneyView(L, map, allPts, pad, alive) {
    if (!map || !allPts || allPts.length < 2) return;
    const bounds = L.latLngBounds(allPts);
    const apply = () => {
      if (!alive()) return;
      try {
        map.invalidateSize();
        map.fitBounds(bounds, { padding: [pad, pad], maxZoom: 14, animate: false });
      } catch (_e) {
      }
    };
    apply();
    requestAnimationFrame(() => {
      apply();
      requestAnimationFrame(apply);
    });
    setTimeout(apply, 200);
  }
  _fmtClock(ms) {
    return ms ? BkkLib.hm(Math.round(ms / 1e3)) : "";
  }
  /* "ma 07:30", "holnap 07:30", or "h 10.20. 07:30" in Budapest time. */
  _whenText(ms) {
    const lang = this._lang();
    const f = tzFields(new Date(ms));
    const today = tzFields(new Date(Date.now()));
    const tomorrow = (() => {
      const ymd = tzShiftYmd(`${today.y}${pad2(today.mo)}${pad2(today.d)}`, 1);
      return { y: Number(ymd.slice(0, 4)), mo: Number(ymd.slice(4, 6)), d: Number(ymd.slice(6, 8)) };
    })();
    const clock = `${pad2(f.h)}:${pad2(f.mi)}`;
    const same = (a, b) => a.y === b.y && a.mo === b.mo && a.d === b.d;
    if (same(f, today)) return `${t(lang, "plannerToday")} ${clock}`;
    if (same(f, tomorrow)) return `${t(lang, "plannerTomorrow")} ${clock}`;
    const day = new Intl.DateTimeFormat(lang === "hu" ? "hu-HU" : "en-GB", {
      timeZone: "Europe/Budapest",
      weekday: "short",
      month: "numeric",
      day: "numeric"
    }).format(new Date(ms));
    return `${day} ${clock}`;
  }
  _legChips(journey) {
    const esc = BkkLib.esc;
    return (journey.legs || []).map((leg) => {
      if (leg.walk) {
        return `<span class="chip-walk"><ha-icon icon="mdi:walk"></ha-icon>${esc(leg.minutes)}</span>`;
      }
      return `<span class="j-badge" style="${esc(this._badgeStyle({ color: leg.color, textcolor: leg.text }))}">${esc(leg.label || "?")}</span>`;
    }).join('<span class="chip-sep">›</span>');
  }
  /* Departure → arrival as a vertical timeline in the line colours. */
  _legTimeline(journey) {
    const lang = this._lang();
    const esc = BkkLib.esc;
    const legs = journey.legs || [];
    const items = [];
    legs.forEach((leg) => {
      if (leg.walk) {
        const walkKey = leg.stationWalk ? "plannerStationWalk" : "plannerWalk";
        items.push(`<li class="tl-walk"><span class="tl-t">${esc(this._fmtClock(leg.startMs))}</span><div class="tl-b"><ha-icon icon="mdi:walk"></ha-icon>${esc(t(lang, walkKey, [leg.minutes, leg.meters]))}</div></li>`);
        return;
      }
      if (leg.waitMin >= 1) {
        items.push(`<li class="tl-wait"><span class="tl-t"></span><div class="tl-b">${esc(t(lang, "plannerWait", leg.waitMin))}</div></li>`);
      }
      const hex = String(leg.color || "").replace("#", "");
      const tone = /^[0-9a-fA-F]{6}$/.test(hex) ? `#${hex}` : "var(--primary-color)";
      items.push(`<li class="tl-ride" style="--c:${esc(tone)}"><span class="tl-t">${esc(this._fmtClock(leg.startMs))}</span><div class="tl-b"><div class="tl-stop">${esc(leg.from || "")}</div><div class="tl-line"><span class="j-badge" style="${esc(this._badgeStyle({ color: leg.color, textcolor: leg.text }))}">${esc(leg.label || "?")}</span><span class="tl-head">${esc(leg.headsign || leg.to || "")}</span><span class="tl-min">${esc(t(lang, "plannerRide", leg.minutes))}</span></div><div class="tl-off">${esc(this._fmtClock(leg.endMs))} ${esc(leg.to || "")}</div></div></li>`);
    });
    const last = legs[legs.length - 1];
    if (last) {
      items.push(`<li class="tl-end"><span class="tl-t">${esc(this._fmtClock(journey.endMs || last.endMs))}</span><div class="tl-b"><div class="tl-stop">${esc(last.to || "")}</div></div></li>`);
    }
    return `<ol class="tl">${items.join("")}</ol>`;
  }
  /* One itinerary: a summary row, and when open its inline map, timeline
     and full-map button. */
  _itineraryHtml(journey, idx, open) {
    const lang = this._lang();
    const esc = BkkLib.esc;
    let times = "";
    if (journey.startMs && journey.endMs) {
      const ref = this._timed() ? this._when.at : Date.now();
      const day = (ms) => {
        const f = tzFields(new Date(ms));
        return Date.UTC(f.y, f.mo - 1, f.d) / 864e5;
      };
      const start = day(journey.startMs) === day(ref) ? this._fmtClock(journey.startMs) : this._whenText(journey.startMs);
      const plus = day(journey.endMs) - day(journey.startMs);
      times = `<span class="itin-time">${esc(start)} – ${esc(this._fmtClock(journey.endMs))}${plus > 0 ? `<sup>+${plus}</sup>` : ""}</span>`;
    }
    const meta = [t(lang, "plannerTransfers", journey.transfers)];
    if (journey.walkMin) meta.push(t(lang, "plannerWalkTotal", journey.walkMin));
    if (journey.waitMin) meta.push(t(lang, "plannerWait", journey.waitMin));
    const body = open ? `<div class="itin-body"><div id="pjmap" class="j-map" hidden></div>${this._legTimeline(journey)}<button type="button" id="pmap">${esc(t(lang, "plannerShowMap"))}</button></div>` : "";
    return `<div class="itin${open ? " open" : ""}"><button type="button" class="itin-head" data-i="${idx}" aria-expanded="${open ? "true" : "false"}"><span class="itin-top">${times}<span class="itin-dur">${esc(t(lang, "plannerDuration", journey.durationMin))}</span></span><span class="itin-legs">${this._legChips(journey)}</span><span class="itin-meta">${esc(meta.join(" · "))}</span></button>${body}</div>`;
  }
  /* What the journey box shows: its class and markup, and whether its map
     button opens the board's first row. Null for nothing. */
  _journeyView() {
    const cfg = this._config || {};
    if (!cfg.stopId || !cfg.destName) return null;
    const lang = this._lang();
    const esc = BkkLib.esc;
    if (this._timed()) {
      const sub = this._when.mode === "arrive" ? t(lang, "plannerRoutesBy", this._whenText(this._when.at)) : t(lang, "plannerRoutesFrom", this._whenText(this._when.at));
      let inner = `<div class="j-title">${esc(t(lang, "plannerRoutes"))}<span class="j-sub">${esc(sub)}</span></div>`;
      const list = this._journeys || [];
      if (this._journeyLoading) {
        inner += `<div class="j-msg">${esc(t(lang, "plannerSearching"))}</div>`;
      } else if (!list.length) {
        inner += `<div class="j-msg">${esc(t(lang, "plannerNoJourneys"))}</div>`;
      } else {
        inner += list.map((j, i) => this._itineraryHtml(j, i, i === this._openJourney)).join("");
      }
      return { className: "journey-plan", html: inner, useBoard: false };
    }
    const visible = this._visibleRows();
    const journey = this._journey;
    const showJourney = !!(journey && journey.legs && journey.legs.length && (!visible.length || BkkLib.journeyBeatsBoard(journey, visible)));
    if (visible.length && !showJourney) {
      return {
        className: "journey-plan j-map-only",
        html: `<button type="button" id="pmap">${esc(t(lang, "plannerShowMap"))}</button>`,
        useBoard: true
      };
    }
    if (!showJourney) return null;
    const rides = (journey && journey.legs || []).filter((leg) => !leg.walk).length;
    const titleKey = rides > 1 ? "plannerJourneyTitle" : rides === 1 ? "plannerJourneyDirect" : "plannerJourneyWalk";
    return {
      className: "journey-plan",
      html: `<div class="j-title">${esc(t(lang, titleKey))}</div>` + this._itineraryHtml(journey, 0, true),
      useBoard: false
    };
  }
  _visibleRows() {
    const nowSec = Math.floor(Date.now() / 1e3);
    return (this._rows || []).filter((r) => departureStillDue(r.depTs, nowSec));
  }
  /* Redrawn only when what it shows changes: a board repaint (an error
     banner, a late hass) keeps the box and its inline map as they are. */
  _paintJourney() {
    const host = this._journeyHost;
    if (!host) return;
    const view = this._journeyView();
    const sig = view ? view.className + "\n" + view.html : "";
    if (sig === this._journeySig && this._journeySigOf === this._journey && host.childNodes.length === (view ? 1 : 0)) return;
    this._journeySig = sig;
    this._journeySigOf = this._journey;
    this._teardownPlannerInlineMap();
    host.innerHTML = "";
    if (!view) return;
    const box = document.createElement("div");
    box.className = view.className;
    box.innerHTML = view.html;
    host.appendChild(box);
    this._bindJourneyBox(box, view.useBoard);
  }
  _bindJourneyBox(box, useBoard) {
    const btn = box.querySelector("#pmap");
    if (btn) {
      btn.addEventListener("click", () => {
        if (!useBoard) return this._openJourneyMap();
        const first = this._visibleRows()[0];
        if (first) return this._openVehicleMap(first);
        this._showMapNotice(t(this._lang(), "mapNoData"));
      });
    }
    box.querySelectorAll(".itin-head").forEach((head) => {
      head.addEventListener("click", () => {
        if (!this._timed()) return;
        const i = Number(head.getAttribute("data-i"));
        if (!Number.isInteger(i) || !this._journeys[i]) return;
        this._openJourney = this._openJourney === i ? -1 : i;
        this._journey = this._journeys[this._openJourney] || null;
        this._paintJourney();
      });
    });
    if (box.querySelector("#pjmap")) this._mountPlannerInlineMap(box);
  }
  async _mountPlannerInlineMap(host) {
    const el = host && host.querySelector("#pjmap");
    if (!el) return;
    try {
      const cfg = this._config || {};
      try {
        await BkkLib.fillJourneyShapes(this._journey);
      } catch (_e) {
      }
      if (!el.isConnected) return;
      const segs = BkkLib.journeyMapSegments(this._journey, cfg, cfg);
      if (!segs.length) {
        el.hidden = true;
        return;
      }
      let L;
      try {
        L = await this._ensureLeaflet();
      } catch (_e) {
        el.hidden = true;
        return;
      }
      if (!el.isConnected) return;
      this._teardownPlannerInlineMap();
      el.hidden = false;
      const start = segs[0].shape[0];
      const map = L.map(el, {
        scrollWheelZoom: false,
        dragging: false,
        touchZoom: false,
        doubleClickZoom: false,
        boxZoom: false,
        keyboard: false,
        preferCanvas: false,
        attributionControl: true,
        zoomControl: false,
        zoomAnimation: false
      }).setView(start, 8);
      this._plannerMap = map;
      L.tileLayer(MAP_TILES, { maxZoom: 20, subdomains: "abc", attribution: MAP_ATTRIBUTION }).addTo(map);
      const allPts = this._drawJourneyPolylines(L, map, segs, { interactive: false });
      map.on("click", () => this._openJourneyMap());
      this._fitJourneyView(L, map, allPts, 16, () => this._plannerMap === map);
    } catch (_e) {
      this._teardownPlannerInlineMap();
      el.hidden = true;
    }
  }
  async _openJourneyMap() {
    const lang = this._lang();
    const cfg = this._config || {};
    if (this._openingMap === "journey") return;
    if (this._mapOverlay && this._openMapKey === "journey") return;
    this._closeVehicleMap();
    this._openingMap = "journey";
    const seq = this._mapSeq;
    try {
      await BkkLib.fillJourneyShapes(this._journey);
    } catch (_e) {
    }
    if (this._mapStale(seq)) return;
    let segs = BkkLib.journeyMapSegments(this._journey, cfg, cfg);
    if (!segs.length && cfg.stopId && cfg.destName) {
      try {
        const journey = await BkkLib.withIndexes(this._config).planJourney(
          this._apiKey(),
          { id: cfg.stopId, name: cfg.stopName || "", lat: cfg.stopLat, lon: cfg.stopLon },
          { id: cfg.destStopId || "", name: cfg.destName || "", lat: cfg.destLat, lon: cfg.destLon },
          this._hass,
          { minutesAfter: clampMinutesAfter(cfg.minutesAfter) }
        );
        if (this._mapStale(seq)) return;
        if (journey) this._journey = journey;
        try {
          await BkkLib.fillJourneyShapes(this._journey);
        } catch (_e) {
        }
        if (this._mapStale(seq)) return;
        segs = BkkLib.journeyMapSegments(this._journey, cfg, cfg);
      } catch (_e) {
      }
    }
    if (this._mapStale(seq)) return;
    if (!segs.length) {
      this._openingMap = "";
      this._showMapNotice(t(lang, "mapNoData"));
      return;
    }
    let L;
    try {
      L = await this._ensureLeaflet();
    } catch (_e) {
      if (this._mapStale(seq)) return;
      this._openingMap = "";
      this._showMapNotice(t(lang, "mapLeafletFail"));
      return;
    }
    if (this._mapStale(seq)) return;
    const overlay = document.createElement("div");
    overlay.className = "ht-map-overlay";
    const title = `${cfg.stopName || ""} → ${cfg.destName || ""}`;
    const footText = t(lang, "plannerJourneySummary", [
      this._journey && this._journey.durationMin,
      this._journey && this._journey.transfers,
      this._journey && this._journey.walkMin,
      this._journey && this._journey.waitMin || 0
    ]);
    overlay.innerHTML = `
      <style>${this._mapChromeCss()}</style>
      <div class="ht-map-panel" role="dialog" aria-modal="true">
        <div class="ht-map-head">
          <div class="meta">${BkkLib.esc(title)}</div>
          <button type="button" class="ht-map-close" aria-label="${BkkLib.esc(t(lang, "mapClose"))}">×</button>
        </div>
        <div class="ht-map-canvas"></div>
        <div class="ht-map-foot">${BkkLib.esc(footText)}</div>
      </div>
    `;
    (document.documentElement || document.body).appendChild(overlay);
    this._mapOverlay = overlay;
    this._openMapKey = "journey";
    this._onMapKey = (ev) => {
      if (ev.key === "Escape") this._closeVehicleMap();
    };
    document.addEventListener("keydown", this._onMapKey);
    overlay.querySelector(".ht-map-close").addEventListener("click", () => this._closeVehicleMap());
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) this._closeVehicleMap();
    });
    const canvas = overlay.querySelector(".ht-map-canvas");
    const start = segs[0].shape[0];
    let map;
    try {
      map = L.map(canvas, { scrollWheelZoom: true, preferCanvas: false }).setView(start, 8);
      L.tileLayer(MAP_TILES, { maxZoom: 20, subdomains: "abc", attribution: MAP_ATTRIBUTION }).addTo(map);
    } catch (_e) {
      this._openingMap = "";
      this._showMapNotice(t(lang, "mapLeafletFail"));
      return;
    }
    this._map = map;
    const allPts = this._drawJourneyPolylines(L, map, segs, { labels: true });
    this._fitJourneyView(L, map, allPts, 36, () => this._map === map);
    this._openingMap = "";
  }
  /* The pickers name origin and destination already; the card keeps its
     own name (or "Tervező") as a title. */
  _header() {
    const cfg = this._config || {};
    return cfg.name || t(this._lang(), "plannerTitle");
  }
  _render() {
    const first = !this._painted;
    super._render();
    const card = this.shadowRoot && this.shadowRoot.querySelector("ha-card");
    if (card) card.classList.add("is-planner");
    if (first) this._mountPickers();
    else this._syncPickerLabels();
    this._ensureDepartureCaption();
    this._ensureDests();
  }
  /* An origin from the YAML or the editor needs its reachable destinations
     too, not only one picked in the card. */
  _ensureDests() {
    const cfg = this._config || {};
    if (!this._pickersMounted || !this._hass || !cfg.stopId || this._destsFor === cfg.stopId) return;
    this._loadDests();
  }
  /* Itineraries sit between the pickers and the departure board. */
  _ensureDepartureCaption() {
    if (!this.shadowRoot || !this._elBody) return;
    if (!this._journeyHost || !this._journeyHost.isConnected) {
      this._journeyHost = document.createElement("div");
      this._journeyHost.className = "pjourney";
      this._elBody.parentNode.insertBefore(this._journeyHost, this._elBody);
    }
    if (this.shadowRoot.getElementById("pdeps")) return;
    const cap = document.createElement("div");
    cap.id = "pdeps";
    cap.className = "deps-label";
    cap.textContent = t(this._lang(), "plannerDepartures");
    this._elBody.parentNode.insertBefore(cap, this._elBody);
  }
  _paint() {
    super._paint();
    const timed = this._timed();
    const cap = this._planEl("pdeps");
    if (cap) cap.hidden = timed || !(this._rows || []).length;
    if (this._elBody) this._elBody.hidden = timed;
    this._paintJourney();
  }
  _planEl(id) {
    return this.shadowRoot ? this.shadowRoot.getElementById(id) : null;
  }
  _mountPickers() {
    if (this._pickersMounted || !this.shadowRoot) return;
    const wrap = this.shadowRoot.querySelector(".wrap");
    const head = this._elHead;
    if (!wrap || !head) return;
    const style = document.createElement("style");
    style.textContent = `
      ha-card.is-planner { overflow: visible; }
      ha-card.is-planner .wrap { padding: 14px 14px 12px; }
      ha-card.is-planner .head {
        font-size: 18px; font-weight: 700; letter-spacing: -0.01em; line-height: 1.25;
        margin: 0 2px 12px; padding: 0;
      }
      ha-card.is-planner .err { margin: 8px 2px 0; }
      ha-card.is-planner .deps-label, .journey-plan .j-title {
        display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap;
        margin: 16px 2px 6px; font-size: 12px; font-weight: 700;
        letter-spacing: 0.06em; text-transform: uppercase; color: var(--secondary-text-color);
      }
      ha-card.is-planner .deps-label[hidden], ha-card.is-planner .body[hidden] { display: none; }
      ha-card.is-planner .body { padding: 0; }
      ha-card.is-planner .msg { padding: 10px 2px; font-size: 14px; }
      ha-card.is-planner table { font-size: 15px; }
      ha-card.is-planner td { padding: 11px 5px; }
      ha-card.is-planner td.route .badge {
        min-width: 2.6em; text-align: center; border-radius: 8px;
        padding: 4px 8px; font-weight: 700;
      }
      @container ht-deps (max-width: ${NARROW_ROWS_PX}px) {
        ha-card.is-planner td { padding: 0; }
        ha-card.is-planner tr { padding: 9px 0; }
      }

      .plan { display: flex; flex-direction: column; gap: 10px; }
      /* Scoped to .plan: a bare .route / .when also matched the departure
         board's td.route and td.when and squeezed the headsign to a letter. */
      .plan .route {
        display: grid; grid-template-columns: 14px minmax(0, 1fr) 40px; column-gap: 10px;
        padding: 10px 8px 10px 12px; border-radius: 16px;
        background: var(--secondary-background-color, rgba(127,127,127,.12));
      }
      .rail { display: flex; flex-direction: column; align-items: center; padding: 30px 0 18px; }
      .rail .dot { width: 12px; height: 12px; border-radius: 50%; box-sizing: border-box; flex: 0 0 auto; }
      .rail .dot.from { background: var(--primary-color); }
      .rail .dot.to { border: 3px solid var(--primary-color); background: var(--card-background-color, #fff); }
      .rail .line {
        flex: 1 1 auto; width: 0; margin: 4px 0;
        border-left: 2px dotted var(--secondary-text-color); opacity: .55;
      }
      .places { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
      .plan .label {
        font-size: 11px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase;
        color: var(--secondary-text-color); margin: 0 0 4px 2px;
      }
      .plan input, .plan select, .plan .picked {
        width: 100%; box-sizing: border-box; height: 40px; margin: 0;
        font: inherit; font-size: 15px; color: var(--primary-text-color);
        background: var(--card-background-color, #fff);
        border: 1px solid var(--divider-color); border-radius: 12px; padding: 0 12px;
      }
      .plan input:focus, .plan select:focus, .plan .picked:focus-visible,
      .seg button:focus-visible, #pswap:focus-visible, .itin-head:focus-visible {
        outline: 2px solid var(--primary-color); outline-offset: 1px;
      }
      .plan input.compact-hide { display: none; }
      .plan .picked {
        display: none; text-align: left; font-weight: 600; cursor: pointer;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-color: transparent;
      }
      .plan .picked.show { display: block; }
      #pswap {
        grid-column: 3; align-self: center; justify-self: center;
        width: 40px; height: 40px; border-radius: 50%; padding: 0; cursor: pointer;
        display: flex; align-items: center; justify-content: center;
        border: 1px solid var(--divider-color); background: var(--card-background-color, #fff);
        color: var(--primary-color);
      }
      #pswap svg { width: 22px; height: 22px; fill: currentColor; }
      #pswap:active { transform: rotate(180deg); transition: transform .2s; }

      .plan .when { display: flex; flex-direction: column; gap: 8px; }
      .seg {
        display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px; padding: 3px;
        border-radius: 12px; background: var(--secondary-background-color, rgba(127,127,127,.12));
      }
      .seg button {
        border: 0; border-radius: 9px; padding: 8px 6px; cursor: pointer;
        font: inherit; font-size: 14px; font-weight: 600;
        background: transparent; color: var(--secondary-text-color);
      }
      .seg button.on {
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        box-shadow: 0 1px 3px rgba(0,0,0,.18);
      }
      .plan .horizon { display: flex; flex-direction: row; align-items: center; gap: 8px; }
      .plan .horizon .label { margin: 0; flex: 0 0 auto; }
      .plan .horizon select, .plan .horizon input { flex: 1 1 auto; min-width: 0; height: 38px; }
      .plan .horizon [hidden] { display: none; }
      .plan #preset {
        flex: 0 0 auto; border: 0; background: transparent; cursor: pointer; padding: 6px 4px;
        font: inherit; font-size: 13px; font-weight: 700; color: var(--primary-color);
      }

      .plan .suggest { margin-top: 6px; display: flex; flex-direction: row; flex-wrap: wrap; gap: 6px; }
      .plan .suggest.hidden, .plan .suggest:empty { display: none; }
      .plan .suggest-row, .plan .suggest-stops { display: contents; }
      .plan .suggest-kind { display: none; }
      .plan .suggest-stop {
        display: inline-flex; align-items: center; gap: 4px; cursor: pointer;
        border: 1px solid var(--divider-color); border-radius: 999px;
        font: inherit; font-size: 12px; font-weight: 600; padding: 4px 10px 4px 6px;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
      }
      .plan .suggest-feed {
        display: inline-flex; align-items: center; justify-content: center;
        width: 16px; height: 16px; border-radius: 50%;
        font-size: 9px; font-weight: 800; line-height: 1;
        background: var(--primary-color); color: var(--text-primary-color, #fff);
      }
      .plan .suggest-list { display: contents; }
      .plan .list { max-height: 220px; overflow: auto; margin-top: 4px; }
      .plan .list:empty { display: none; }
      .plan .hit {
        display: block; width: 100%; text-align: left; cursor: pointer; margin-top: 4px;
        font: inherit; font-size: 14px; font-weight: 600; padding: 9px 12px;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        border: 1px solid var(--divider-color); border-radius: 10px;
      }
      .plan .hint { display: block; padding: 6px 4px; font-size: 13px; color: var(--secondary-text-color); }
      .plan #pdest.show ~ #pdhits .hint { display: none; }
      .itin-time sup { font-size: 11px; font-weight: 700; margin-left: 1px; color: var(--secondary-text-color); }

      .journey-plan .j-title { margin-top: 16px; }
      .journey-plan .j-sub { font-weight: 600; text-transform: none; letter-spacing: 0; }
      .journey-plan .j-msg { padding: 12px 2px; font-size: 14px; color: var(--secondary-text-color); }
      .journey-plan.j-map-only { margin: 12px 0 0; }
      .itin {
        border: 1px solid var(--divider-color); border-radius: 14px; overflow: hidden;
        background: var(--card-background-color, #fff);
      }
      .itin + .itin { margin-top: 8px; }
      .itin.open { border-color: var(--primary-color); }
      .itin-head {
        display: flex; flex-direction: column; gap: 6px; width: 100%; text-align: left;
        border: 0; background: transparent; color: inherit; font: inherit;
        padding: 10px 12px; cursor: pointer;
      }
      .itin-top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
      .itin-time { font-size: 18px; font-weight: 700; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
      .itin-dur { font-size: 14px; font-weight: 700; white-space: nowrap; }
      .itin-legs { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }
      .itin-meta { font-size: 12px; color: var(--secondary-text-color); }
      .j-badge {
        display: inline-block; flex: 0 0 auto; border-radius: 6px; padding: 2px 7px;
        font-size: 13px; font-weight: 700; line-height: 1.35; white-space: nowrap;
      }
      .chip-walk {
        display: inline-flex; align-items: center; gap: 1px; font-size: 12px; font-weight: 600;
        color: var(--secondary-text-color);
      }
      .chip-walk ha-icon, .tl ha-icon { --mdc-icon-size: 16px; }
      .chip-sep { color: var(--secondary-text-color); font-size: 14px; }
      .itin-body { padding: 0 12px 12px; }
      .journey-plan .j-map,
      .journey-plan .j-map.leaflet-container {
        height: 200px; min-height: 200px; margin: 0 0 12px; border-radius: 12px;
        overflow: hidden; position: relative; cursor: pointer;
        background: var(--secondary-background-color, #eee);
      }
      .journey-plan .j-map[hidden] { display: none; }
      .journey-plan .j-map.leaflet-container,
      .journey-plan .j-map .leaflet-container{width:100%;height:100%;min-height:200px;overflow:hidden}
      .journey-plan .j-map .leaflet-pane,.journey-plan .j-map .leaflet-tile,.journey-plan .j-map .leaflet-marker-icon,.journey-plan .j-map .leaflet-marker-shadow,.journey-plan .j-map .leaflet-tile-container,.journey-plan .j-map .leaflet-zoom-box,.journey-plan .j-map .leaflet-image-layer,.journey-plan .j-map .leaflet-layer{position:absolute;left:0;top:0}
      .journey-plan .j-map .leaflet-pane>svg,.journey-plan .j-map .leaflet-pane>canvas{position:absolute}
      .journey-plan .j-map svg,.journey-plan .j-map .leaflet-container svg{max-width:none!important;max-height:none!important}
      .journey-plan .j-map .leaflet-tile{max-width:none!important}
      .journey-plan .j-map .leaflet-tile-pane{z-index:200}
      .journey-plan .j-map .leaflet-overlay-pane{z-index:400}
      .journey-plan .j-map .leaflet-marker-pane{z-index:600}
      .journey-plan .j-map .leaflet-top,.journey-plan .j-map .leaflet-bottom{position:absolute;z-index:1000;pointer-events:none}
      .journey-plan .j-map .leaflet-top{top:0}
      .journey-plan .j-map .leaflet-bottom{bottom:0}
      .journey-plan .j-map .leaflet-left{left:0}
      .journey-plan .j-map .leaflet-right{right:0}
      .journey-plan .j-map .leaflet-control{position:relative;z-index:800;float:left;clear:both;pointer-events:auto}
      .journey-plan .j-map .leaflet-right .leaflet-control{float:right}
      .journey-plan .j-map .leaflet-control-attribution{margin:0;padding:0 5px;font-size:10px;line-height:1.4;color:#333;background:rgba(255,255,255,.72)}
      .journey-plan .j-map .leaflet-control-attribution a{color:#0078a8;text-decoration:none}
      .journey-plan .j-map .leaflet-attribution-flag{display:inline!important;vertical-align:baseline!important;width:1em;height:.6669em}
${this._stopPinCss()}
      .journey-plan #pmap {
        margin-top: 10px; border: 0; cursor: pointer; font: inherit; font-size: 13px; font-weight: 700;
        border-radius: 999px; padding: 8px 14px;
        background: var(--primary-color); color: var(--text-primary-color, #fff);
      }
      .journey-plan.j-map-only #pmap { margin: 0; }

      .tl { list-style: none; margin: 0; padding: 0; }
      .tl li { display: grid; grid-template-columns: 44px minmax(0, 1fr); column-gap: 10px; }
      .tl .tl-t {
        font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums;
        text-align: right; padding-top: 1px;
      }
      .tl .tl-b { position: relative; padding: 0 0 12px 14px; min-width: 0; font-size: 14px; }
      .tl .tl-b::before {
        content: ''; position: absolute; left: 0; top: 4px; bottom: -4px;
        border-left: 4px solid var(--c, var(--divider-color)); border-radius: 2px;
      }
      .tl .tl-walk .tl-b::before, .tl .tl-wait .tl-b::before {
        border-left: 3px dotted var(--secondary-text-color); left: 1px; opacity: .6;
      }
      .tl .tl-walk .tl-b, .tl .tl-wait .tl-b { color: var(--secondary-text-color); font-size: 13px; }
      .tl .tl-walk ha-icon { vertical-align: -3px; margin-right: 2px; }
      .tl .tl-end .tl-b::before {
        top: 3px; bottom: auto; width: 10px; height: 10px; left: -3px; border: 3px solid var(--primary-color);
        border-radius: 50%; box-sizing: border-box; background: var(--card-background-color, #fff);
      }
      .tl .tl-end .tl-b { padding-bottom: 0; }
      .tl .tl-stop { font-weight: 700; overflow-wrap: anywhere; }
      .tl .tl-line { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 4px 0 2px; }
      .tl .tl-head { flex: 1 1 auto; min-width: 0; color: var(--secondary-text-color); }
      .tl .tl-min { font-weight: 700; font-size: 13px; }
      .tl .tl-off { font-size: 12px; color: var(--secondary-text-color); }
    `;
    this.shadowRoot.appendChild(style);
    const lang = this._lang();
    const esc = BkkLib.esc;
    const plan = document.createElement("div");
    plan.className = "plan";
    plan.innerHTML = `
      <div class="route journey">
        <div class="rail spine"><span class="dot from"></span><span class="line"></span><span class="dot to"></span></div>
        <div class="places">
          <div class="place">
            <div class="label" id="plab1">${esc(t(lang, "plannerStep1"))}</div>
            <input id="pq" type="search" placeholder="${esc(t(lang, "plannerSearchPlaceholder"))}">
            <button type="button" class="picked" id="pstop"></button>
            <div class="suggest" id="pfav"></div>
            <div class="list" id="phits"></div>
          </div>
          <div class="place">
            <div class="label" id="plab2">${esc(t(lang, "plannerStep2"))}</div>
            <input id="pdq" type="search" placeholder="${esc(t(lang, "plannerDestPlaceholder"))}">
            <button type="button" class="picked" id="pdest"></button>
            <div class="list" id="pdhits"></div>
          </div>
        </div>
        <button id="pswap" type="button" aria-label="${esc(t(lang, "plannerSwap"))}" title="${esc(t(lang, "plannerSwap"))}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9,3L5,7H8V14H10V7H13M16,17V10H14V17H11L15,21L19,17H16Z"/></svg>
        </button>
      </div>
      <div class="when">
        <div class="seg" role="radiogroup" id="pwhen" aria-label="${esc(t(lang, "plannerWhen"))}">
          <button type="button" role="radio" data-when="now"></button>
          <button type="button" role="radio" data-when="depart"></button>
          <button type="button" role="radio" data-when="arrive"></button>
        </div>
        <div class="horizon">
          <div class="label" id="plab3">${esc(t(lang, "plannerStep3"))}</div>
          <select id="pmin" aria-label="${esc(t(lang, "plannerStep3"))}"></select>
          <input id="pat" type="datetime-local" step="300" hidden aria-label="${esc(t(lang, "plannerWhen"))}">
          <button id="preset" type="button">${esc(t(lang, "plannerReset"))}</button>
        </div>
      </div>
    `;
    wrap.insertBefore(plan, head.nextSibling);
    this._pickersMounted = true;
    this._bindPickers();
    this._syncPickerLabels();
    this._paintDestHits("");
  }
  _bindPickers() {
    const q = this._planEl("pq");
    const dq = this._planEl("pdq");
    const reset = this._planEl("preset");
    const swap = this._planEl("pswap");
    if (q) {
      q.addEventListener("input", () => {
        clearTimeout(this._searchTimer);
        this._searchTimer = setTimeout(() => this._searchOrigin(q.value.trim()), 280);
      });
      q.addEventListener("blur", () => {
        if ((this._config || {}).stopName && !q.value.trim()) q.classList.add("compact-hide");
      });
    }
    if (dq) {
      dq.addEventListener("input", () => {
        clearTimeout(this._destTimer);
        this._destTimer = setTimeout(() => this._searchDest(dq.value.trim()), 280);
      });
      dq.addEventListener("focus", () => this._searchDest(dq.value.trim()));
      dq.addEventListener("blur", () => {
        if ((this._config || {}).destName && !dq.value.trim()) dq.classList.add("compact-hide");
      });
    }
    const stop = this._planEl("pstop");
    const dest = this._planEl("pdest");
    if (stop && q) {
      stop.addEventListener("click", () => {
        q.classList.remove("compact-hide");
        q.focus();
      });
    }
    if (dest && dq) {
      dest.addEventListener("click", () => {
        dq.classList.remove("compact-hide");
        dq.focus();
      });
    }
    if (reset) reset.addEventListener("click", () => this._resetPlan());
    if (swap) swap.addEventListener("click", () => this._swapPlan());
    const seg = this._planEl("pwhen");
    if (seg) {
      seg.querySelectorAll("button[data-when]").forEach((btn) => {
        btn.addEventListener("click", () => this._setWhen(btn.getAttribute("data-when")));
      });
    }
    const at = this._planEl("pat");
    if (at) {
      at.addEventListener("change", () => {
        const ms = this._parseWhenInput(at.value);
        if (!ms) return;
        this._applyWhen({ mode: this._when.mode === "now" ? "depart" : this._when.mode, at: ms });
      });
    }
    this._paintMinutesAfter();
  }
  /* Most: live board. Indulás / Érkezés: itineraries for the picked time,
     starting from the next five-minute mark. */
  _setWhen(mode) {
    if (mode !== "depart" && mode !== "arrive") {
      this._applyWhen({ mode: "now", at: 0 });
      return;
    }
    let at = this._when.at;
    if (!(at > Date.now() - 36e5)) {
      const step = 5 * 6e4;
      at = Math.ceil(Date.now() / step) * step;
    }
    this._applyWhen({ mode, at });
  }
  _applyWhen(when) {
    this._when = when;
    this._journeyKey = "";
    this._journey = null;
    this._journeys = [];
    this._journeysDone = false;
    this._journeyLoading = false;
    this._syncWhen();
    this._reloadSafe();
  }
  /* datetime-local values are Budapest wall clock, like every time here. */
  _parseWhenInput(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(value || ""));
    if (!m) return 0;
    return tzEpochSec(`${m[1]}${m[2]}${m[3]}`, Number(m[4]) * 60 + Number(m[5])) * 1e3;
  }
  _whenInputValue(ms) {
    const f = tzFields(new Date(ms));
    return `${f.y}-${pad2(f.mo)}-${pad2(f.d)}T${pad2(f.h)}:${pad2(f.mi)}`;
  }
  _syncWhen() {
    const lang = this._lang();
    const timed = this._timed();
    const labels = { now: "plannerNow", depart: "plannerDepartAt", arrive: "plannerArriveBy" };
    const seg = this._planEl("pwhen");
    if (seg) {
      seg.setAttribute("aria-label", t(lang, "plannerWhen"));
      seg.querySelectorAll("button[data-when]").forEach((btn) => {
        const mode = btn.getAttribute("data-when");
        const on = mode === (this._when.mode || "now");
        btn.textContent = t(lang, labels[mode]);
        btn.classList.toggle("on", on);
        btn.setAttribute("aria-checked", on ? "true" : "false");
      });
    }
    const sel = this._planEl("pmin");
    const lab = this._planEl("plab3");
    const at = this._planEl("pat");
    if (sel) sel.hidden = timed;
    if (lab) lab.hidden = timed;
    if (at) {
      at.hidden = !timed;
      if (timed && this.shadowRoot.activeElement !== at) at.value = this._whenInputValue(this._when.at);
    }
  }
  /* A new login reads the suggestions at once; a failed read again only
     after HISTORY_RETRY_MS. */
  _historyDue(conn) {
    return this._historyFor !== conn || this._historyRetryAt > 0 && Date.now() >= this._historyRetryAt;
  }
  /* The stops this user picks most, from their own Home Assistant storage
     (see stop-history.js); nothing until they have picked one. */
  _paintFav() {
    const box = this._planEl("pfav");
    if (!box) return;
    const cfg = this._config || {};
    if (!this._hass) {
      box.innerHTML = "";
      return;
    }
    const conn = this._hass.connection || null;
    if (this._historyDue(conn)) {
      this._historyFor = conn;
      this._historyRetryAt = 0;
      const hass = this._hass;
      const retry = () => {
        if (this._historyFor === conn) this._historyRetryAt = Date.now() + HISTORY_RETRY_MS;
      };
      BkkLib.loadStopHistory(hass).then((list2) => {
        if (this._historyFor !== conn) return;
        this._historyList = list2;
        this._paintFav();
        if (!BkkLib.stopHistoryKept(hass)) retry();
      }).catch(retry);
    }
    box.innerHTML = "";
    const shortName = (name) => String(name || "").replace(/^Budapest,\s*/, "").replace(/^Budapest-/, "").replace(/\s+aut\u00f3busz-p\u00e1lyaudvar$/, "").replace(/\s+vas\u00fat\u00e1llom\u00e1s$/, "").replace(/\s+p\u00e1lyaudvar$/, "");
    const list = BkkLib.frequentStops(this._historyList || [], { excludeId: cfg.stopId, limit: 6 });
    if (!list.length) return;
    const marks = { mav: "M", volan: "V", bkk: "B", city: "H" };
    const row = document.createElement("div");
    row.className = "suggest-row";
    const stops = document.createElement("span");
    stops.className = "suggest-stops";
    list.forEach((fav) => {
      const mark = marks[BkkLib.stopIdFamily(fav)] || "";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "suggest-stop";
      button.title = fav.label || fav.name;
      if (mark) {
        const feed = document.createElement("span");
        feed.className = "suggest-feed";
        feed.textContent = mark;
        button.appendChild(feed);
      }
      button.appendChild(document.createTextNode(shortName(fav.name)));
      button.addEventListener("click", () => this._pickOrigin(fav));
      stops.appendChild(button);
    });
    row.appendChild(stops);
    box.appendChild(row);
  }
  _syncPickerLabels() {
    const cfg = this._config || {};
    const lang = this._lang();
    const stop = this._planEl("pstop");
    const dest = this._planEl("pdest");
    const q = this._planEl("pq");
    const dq = this._planEl("pdq");
    if (stop) {
      stop.textContent = cfg.stopName || "";
      stop.classList.toggle("show", !!cfg.stopName);
    }
    const fav = this._planEl("pfav");
    if (fav) fav.classList.toggle("hidden", !!cfg.stopName);
    const sel = this._planEl("pmin");
    if (sel) sel.setAttribute("aria-label", t(lang, "plannerStep3"));
    const at = this._planEl("pat");
    if (at) at.setAttribute("aria-label", t(lang, "plannerWhen"));
    if (dest) {
      dest.textContent = cfg.destName || "";
      dest.classList.toggle("show", !!cfg.destName);
    }
    if (q) {
      q.placeholder = t(lang, "plannerSearchPlaceholder");
      const editing = this.shadowRoot.activeElement === q || !!(q.value || "").trim();
      q.classList.toggle("compact-hide", !!cfg.stopName && !editing);
    }
    if (dq) {
      dq.placeholder = t(lang, "plannerDestPlaceholder");
      const editing = this.shadowRoot.activeElement === dq || !!(dq.value || "").trim();
      dq.classList.toggle("compact-hide", !!cfg.destName && !editing);
    }
    this._paintFav();
    const lab1 = this._planEl("plab1");
    const lab2 = this._planEl("plab2");
    if (lab1) lab1.textContent = t(lang, "plannerStep1");
    if (lab2) lab2.textContent = t(lang, "plannerStep2");
    const lab3 = this._planEl("plab3");
    if (lab3) lab3.textContent = t(lang, "plannerStep3");
    const deps = this._planEl("pdeps");
    if (deps) deps.textContent = t(lang, "plannerDepartures");
    const swap = this._planEl("pswap");
    const reset = this._planEl("preset");
    if (swap) {
      swap.setAttribute("aria-label", t(lang, "plannerSwap"));
      swap.setAttribute("title", t(lang, "plannerSwap"));
    }
    if (reset) reset.textContent = t(lang, "plannerReset");
    this._paintMinutesAfter();
    this._syncWhen();
    this._repaintHints();
  }
  _paintMinutesAfter() {
    const sel = this._planEl("pmin");
    if (!sel || sel.tagName !== "SELECT") return;
    const current = clampMinutesAfter(this._config && this._config.minutesAfter);
    if (!sel.options.length) {
      MINUTES_AFTER_PRESETS.forEach((n) => {
        const o = document.createElement("option");
        o.value = String(n);
        sel.appendChild(o);
      });
      sel.addEventListener("change", () => {
        const n = clampMinutesAfter(sel.value);
        this._config = Object.assign({}, this._config, { minutesAfter: n });
        this._reloadSafe();
      });
    }
    if (!Array.from(sel.options).some((o) => Number(o.value) === current)) {
      const o = document.createElement("option");
      o.value = String(current);
      const after = Array.from(sel.options).find((x) => Number(x.value) > current);
      sel.insertBefore(o, after || null);
    }
    const lang = this._lang();
    Array.from(sel.options).forEach((o) => {
      o.textContent = t(lang, "plannerHorizon", Number(o.value));
    });
    sel.value = String(current);
  }
  _swapPlan() {
    const cfg = this._config || {};
    const newOriginId = cfg.destStopId;
    const newOriginName = cfg.destName;
    if (!cfg.stopId || !newOriginId || !newOriginName) return;
    this._config = Object.assign({}, cfg, {
      stopId: newOriginId,
      stopName: newOriginName,
      stopLat: cfg.destLat,
      stopLon: cfg.destLon,
      stopKind: cfg.destKind || "",
      destKey: BkkLib.stationKey(cfg.stopName || ""),
      destName: cfg.stopName || "",
      destStopId: cfg.stopId,
      destLat: cfg.stopLat,
      destLon: cfg.stopLon,
      destKind: cfg.stopKind || "",
      routeIds: []
    });
    this._invalidatePlan();
    this._dests = [];
    this._destRoutes = {};
    this._clearPickerInputs();
    this._paint();
    this._reloadSafe();
    this._loadDests();
  }
  _resetPlan() {
    this._when = { mode: "now", at: 0 };
    this._journeys = [];
    this._dests = [];
    this._destRoutes = {};
    this._destsFor = "";
    this._destSeq += 1;
    this._destsLoading = false;
    this._config = Object.assign({}, this._config, {
      stopId: "",
      stopName: "",
      stopKind: "",
      destKey: "",
      destName: "",
      destStopId: "",
      destKind: "",
      routeIds: []
    });
    this._invalidatePlan();
    this._clearPickerInputs();
    this._paintDestHits("");
    this._paint();
  }
  /* After a pick, a swap or a reset: both fields empty, the origin's hits
     gone, the picked names on the buttons. */
  _clearPickerInputs() {
    const q = this._planEl("pq");
    const dq = this._planEl("pdq");
    const hits = this._planEl("phits");
    if (q) q.value = "";
    if (dq) dq.value = "";
    if (hits) hits.innerHTML = "";
    this._syncPickerLabels();
  }
  async _searchOrigin(q) {
    const box = this._planEl("phits");
    if (!box) return;
    const seq = ++this._searchSeq;
    if (!q || q.length < 2) {
      box.innerHTML = "";
      return;
    }
    try {
      this._err = "";
      if (!this._hasFutar()) await this._fillKey();
      if (seq !== this._searchSeq) return;
      const hits = await BkkLib.withIndexes(this._config).searchPlannerStops(this._apiKey(), q);
      if (seq !== this._searchSeq) return;
      if (!hits.length) {
        this._hint(box, "mode.empty");
        return;
      }
      box.innerHTML = hits.map((s) => `<button class="hit" type="button" data-id="${BkkLib.esc(s.id)}">${BkkLib.esc(BkkLib.plannerHitLabel(s, this._lang()))}</button>`).join("");
      box.querySelectorAll(".hit").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.getAttribute("data-id");
          const st = hits.find((h) => h.id === id);
          if (st) this._pickOrigin(st);
        });
      });
    } catch (err) {
      if (seq !== this._searchSeq) return;
      this._showErr(err);
    }
  }
  /* Every picked stop counts towards the user's suggestions. */
  _rememberStop(stop) {
    const hass = this._hass;
    BkkLib.recordStop(hass, stop).then((list) => {
      this._historyList = list;
      this._paintFav();
      if (!BkkLib.stopHistoryKept(hass)) this._historyRetryAt = Date.now() + HISTORY_RETRY_MS;
    }).catch(() => {
    });
  }
  async _pickOrigin(stop) {
    this._rememberStop(stop);
    this._config = Object.assign({}, this._config, {
      stopId: stop.id,
      stopName: stop.name,
      stopLat: stop.lat,
      stopLon: stop.lon,
      stopKind: stop.kind || "",
      destKey: "",
      destName: "",
      destStopId: "",
      destLat: void 0,
      destLon: void 0,
      destKind: "",
      routeIds: []
    });
    this._invalidatePlan();
    this._dests = [];
    this._destRoutes = {};
    this._clearPickerInputs();
    this._paint();
    await this._loadDests();
  }
  async _loadDests() {
    const cfg = this._config || {};
    if (!cfg.stopId) return;
    this._destsFor = cfg.stopId;
    const seq = ++this._destSeq;
    this._dests = [];
    this._destRoutes = {};
    this._destsLoading = true;
    this._paintDestHits("");
    try {
      if (!this._hasFutar()) await this._fillKey();
      const placeOrigin = /^(geo:|node\/)/i.test(String(cfg.stopId || "")) || cfg.stopKind === "address" || cfg.stopKind === "place";
      if (placeOrigin) {
        this._dests = [];
        this._destRoutes = {};
      } else {
        const out = await BkkLib.withIndexes(this._config).reachableDests(
          this._apiKey(),
          this._tripCache || {},
          { id: cfg.stopId, name: cfg.stopName || "" },
          "all",
          ""
        );
        if (seq !== this._destSeq) return;
        this._dests = out && out.dests || [];
        this._destRoutes = out && out.destRoutes || {};
      }
    } catch (err) {
      if (seq !== this._destSeq) return;
      this._showErr(err);
      this._dests = [];
      this._destRoutes = {};
    } finally {
      if (seq === this._destSeq) {
        this._destsLoading = false;
        this._searchDest((this._planEl("pdq") || {}).value || "");
      }
    }
  }
  _idsForDest(keys) {
    const raw = [];
    (Array.isArray(keys) ? keys : [keys]).forEach((key) => {
      (this._destRoutes[key] || []).forEach((id) => {
        if (raw.indexOf(id) < 0) raw.push(id);
      });
    });
    const out = [];
    raw.forEach((id) => {
      if (!id) return;
      out.push(id);
      const s = String(id);
      if (s.indexOf("gtfs:") !== 0 && !/^BKK_/i.test(s) && !/^volan_/i.test(s) && !/^hkir_/i.test(s)) {
        out.push("gtfs:" + s);
      }
    });
    return out;
  }
  async _searchDest(q) {
    const box = this._planEl("pdhits");
    if (!box) return;
    const cfg = this._config || {};
    if (!cfg.stopId) {
      this._hint(box, "plannerPickAll");
      return;
    }
    const seq = ++this._destSearchSeq;
    if (!q || q.length < 2) {
      this._paintDestHits(q || "");
      return;
    }
    try {
      if (!this._hasFutar()) await this._fillKey();
      const extras = await BkkLib.withIndexes(this._config).searchPlannerStops(this._apiKey(), q);
      if (seq !== this._destSearchSeq) return;
      const hits = BkkLib.destHitsForQuery(this._dests, q, extras);
      if (!hits.length && this._destsLoading) {
        this._hint(box, "destLoading");
        return;
      }
      this._renderDestHits(hits, "mode.empty");
    } catch (err) {
      if (seq !== this._destSearchSeq) return;
      this._showErr(err);
    }
  }
  _renderDestHits(hits, emptyKey) {
    const box = this._planEl("pdhits");
    if (!box) return;
    this._destHits = hits || [];
    if (!this._destHits.length) {
      this._hint(box, emptyKey);
      return;
    }
    const shown = this._destHits.slice(0, 20);
    box.innerHTML = shown.map((d, i) => `<button class="hit" type="button" data-ix="${i}" data-key="${BkkLib.esc(d.key)}" data-id="${BkkLib.esc(d.id || "")}">${BkkLib.esc(BkkLib.plannerHitLabel(d, this._lang()))}</button>`).join("");
    box.querySelectorAll(".hit").forEach((btn) => {
      btn.addEventListener("click", () => {
        const d = shown[Number(btn.getAttribute("data-ix"))];
        if (d) this._pickDest(Object.assign({}, d));
      });
    });
  }
  _paintDestHits(q) {
    const box = this._planEl("pdhits");
    if (!box) return;
    const cfg = this._config || {};
    if (!cfg.stopId) {
      this._hint(box, "plannerPickAll");
      return;
    }
    if (this._destsLoading) {
      this._hint(box, "destLoading");
      return;
    }
    if (!this._dests.length) {
      this._hint(box, "plannerTypeDest");
      return;
    }
    const hits = BkkLib.destHitsForQuery(this._dests, q, []);
    this._renderDestHits(hits, "mode.destEmpty");
  }
  _pickDest(dest) {
    this._rememberStop(dest);
    const destKey = BkkLib.splitCityPrefix(dest.name || "") ? BkkLib.norm(dest.name) : dest.key;
    const routeKeys = dest.routeKeys && dest.routeKeys.length ? dest.routeKeys : [dest.key];
    this._config = Object.assign({}, this._config, {
      destKey,
      destName: dest.name,
      destStopId: dest.id || "",
      destLat: dest.lat,
      destLon: dest.lon,
      destKind: dest.kind || "",
      routeIds: this._idsForDest(routeKeys)
    });
    this._invalidatePlan();
    const dq = this._planEl("pdq");
    const hits = this._planEl("pdhits");
    if (dq) dq.value = "";
    if (hits) hits.innerHTML = "";
    this._syncPickerLabels();
    this._reloadSafe();
  }
};

// src/planner-editor.js
var BKKPlannerCardEditor = class extends BKKHopCardEditor {
  _mode() {
    return "all";
  }
  /* The planner's pair changes in the card itself: an "A → B" name baked
     in here would be wrong after the first pick there. Its title is its own. */
  _autoName() {
    return false;
  }
  setConfig(config) {
    super.setConfig(config);
    this._hideModeControls();
  }
  _hideModeControls() {
    const row = this._el("modeSwitches");
    if (row) row.style.display = "none";
    const city = this._el("cityWrap");
    if (city) city.style.display = "none";
  }
  _syncMode() {
    super._syncMode();
    this._hideModeControls();
    const hint = this._el("modeHint");
    if (hint) hint.textContent = t(this._lang(), "plannerPickAll");
    const lblStop = this._el("lblStop");
    if (lblStop) lblStop.textContent = t(this._lang(), "plannerStep1");
    const lblDest = this._el("lblDest");
    if (lblDest) lblDest.textContent = t(this._lang(), "plannerStep2");
  }
};

// src/index.js
var ELEMENTS = [
  [BKK_HOP_EDITOR, BKKHopCardEditor],
  [BKK_HOP_EDITOR_ALIAS, class extends BKKHopCardEditor {
  }],
  [BKK_PLANNER_EDITOR, BKKPlannerCardEditor],
  [BKK_PLANNER_EDITOR_ALIAS, class extends BKKPlannerCardEditor {
  }],
  [BKK_HOP_TAG, BKKHopCard],
  [BKK_HOP_TAG_ALIAS, class extends BKKHopCard {
  }],
  [BKK_PLANNER_TAG, BKKPlannerCard],
  [BKK_PLANNER_TAG_ALIAS, class extends BKKPlannerCard {
  }]
];
function registerElements() {
  ELEMENTS.forEach(([tag, cls]) => {
    if (customElements.get(tag)) return;
    try {
      customElements.define(tag, cls);
    } catch (err) {
      console.warn(`Hungarian transport: could not register <${tag}>`, err);
    }
  });
}
var haRoot = typeof document !== "undefined" && document.querySelector ? document.querySelector("home-assistant, hc-main") : null;
if (haRoot && !customElements.get(haRoot.localName)) {
  customElements.whenDefined(haRoot.localName).then(registerElements, registerElements);
} else {
  registerElements();
}
console.info(
  `%c HUNGARIAN-TRANSPORT %c ${CARD_VERSION} `,
  "color:#fff;background:#0f6fc6;font-weight:700",
  "color:#0f6fc6;background:#fff;font-weight:700"
);
globalThis.HungarianTransportLib = BkkLib;
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === BKK_HOP_TAG)) {
  window.customCards.push({
    type: BKK_HOP_TAG,
    name: "Hungarian transport",
    description: "BKK, MÁV, Volán and local-city departures between two stops",
    preview: false
  });
}
if (!window.customCards.some((c) => c.type === BKK_PLANNER_TAG)) {
  window.customCards.push({
    type: BKK_PLANNER_TAG,
    name: "Hungarian transit stop planner",
    description: "Pick two stops; every BKK, Volán and MÁV service between them is listed",
    preview: false
  });
}
