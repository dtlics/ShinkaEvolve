"""LER campaign runner for the q70_ring_shuttle head-to-head.

Measures logical error rate vs physical error rate for two or more PLANS under
ONE fixed decoder, so that the RATIO between plans is meaningful even though
absolute numbers are not comparable across decoders (the paper's curve is
beam-search decoded; ours is BP based).

TWO SAMPLING MODES
------------------
independent
    The obvious thing: for each (plan, p, observable) sample shots from that
    plan's stim circuit, decode, count failures until `--target-errors` or
    `--max-shots`.  Relative error on a ratio of two arms ~ sqrt(2/N) for N
    errors per arm, so resolving a ~11% effect at 3 sigma needs ~1500
    errors/arm.  Robust, simple, expensive.

paired      (DEFAULT -- ~100x cheaper for the ratio; see design notes)
    Exploits a measured fact about this task: for two plans that differ only
    in transport-round counts, the two detector error models have IDENTICAL
    mechanism supports (31710 of them) and differ ONLY in the per-mechanism
    probabilities (17 distinct ratios, all in [1.000, 1.051] for 424-vs-244).
    So we sample ONE error configuration x per shot from the reference arm's
    DEM, decode it ONCE with ONE fixed decoder, and reweight:

        LER_ref = E_ref[F(x)]
        LER_arm = E_ref[ w(x) * F(x) ],
        w(x)    = P_arm(x)/P_ref(x) = exp( x . c + C )
        c_i     = log(p_i^arm/p_i^ref) - log((1-p_i^arm)/(1-p_i^ref))
        C       = sum_i log((1-p_i^arm)/(1-p_i^ref))

    R = LER_arm/LER_ref is then exactly the sample mean of w over the FAILING
    shots, with standard error sd(w|F)/sqrt(N_fail) -- versus sqrt(2/N_fail)
    for independent arms.  Measured sd(log w) unconditionally: 0.078 at
    p=2e-3, 0.110 at p=4e-3, so the ratio costs ~100x fewer failures.

    ONE decoder, not one per arm.  That is both the protocol the comparison
    requires (absolute LER is not comparable across decoders; a ratio under
    one fixed decoder is) and a hard practical necessity: relay-BP draws its
    relay gammas at random, so per-arm decoders make F_arm(x) != F_ref(x) for
    reasons unrelated to the plans.  Measured: with per-arm decoders on
    IDENTICAL faults at p=4e-3 the two arms reported 17 vs 11 raw failures --
    pure decoder noise, swamping the ~20% real effect.  `--decoder-dem`
    picks whose priors the single decoder uses; running it once per arm
    bounds the resulting prior-mismatch systematic.

    Because the reweighting is free, ONE decode pass measures as many arms as
    you like.  Adding 300/600/1000-transport-round "lever arms" (build them
    with paper_equiv_plan.py --rounds N) measures the transport-channel
    sensitivity  eta_T = d log LER / d log(transport rounds)  over a wide
    contrast at no extra decoding cost; the 424-vs-244 ratio then follows
    from eta_T with much better precision than measuring it head-on.

    Sampling is done from the DEM rather than the circuit (we need to know
    which mechanisms fired).  For Pauli noise the DEM is an exact description
    of the circuit's (detector, observable) distribution; `--validate-sampler`
    checks that against stim's own circuit sampler (measured agreement: 0.04
    sigma on the mean detector rate, per-detector z-scores ~ N(0,1)).

CHECKPOINTING
    The full result dict is rewritten atomically after EVERY batch, so a kill
    loses at most one batch.  Re-running with the same --out resumes.

MEASURED DESIGN CONSTANTS  (24-core box, 2026-07-30, X observable, 9 SECs)
    decoder choice, p=4e-3, ours_244:
        relay-bp (rayon)   158 ms/shot   LER/shot 4.17e-3
        bposd(osd_order=0) 3701 ms/shot  LER/shot 3.52e-1   <- 84x weaker
        mem-bp(100)        22 ms/shot    LER/shot 2.07e-1
    the DEM is NOT decomposable, so pymatching / beliefmatching cannot run.
    parallelism, p=2e-3, 1024 shots:
        serial 1.12 shot/s | relay-bp rayon 12.68 shot/s (11.36x)
        sinter x6 2.74x, x12 4.21x, x24 4.92x  -> sinter LOSES to rayon here
    throughput vs p (relay-bp rayon): 79 ms/shot @2e-3, 91 @3e-3, 158 @4e-3
    LER/shot (ours, 244 rounds): 4.17e-3 @4e-3, ~4.2e-4 @3e-3
    exposure-sensitivity exponent at p=4e-3, from 4 lever arms spanning
    0.75%..10.1% exposure change: eta = 7.49 +- 0.6, CONSTANT across the range.

RECOMMENDED CAMPAIGN
    --p 3.0e-3 3.5e-3 4.0e-3 4.5e-3 --obs X Z --target-errors 60
    --plans ours=ours_244.json folded=lever_300.json paper=paper_equiv_424.json
            lev600=lever_600.json lev1000=lever_1000.json
    projected ~14.5 h wall clock; ~4.8 h if only the RATIO is needed
    (20 failures/point already gives >5 sigma).

USAGE
    python ler_campaign.py --plans ours=ours_244.json paper=paper_equiv_424.json \
        --p 6e-3 5e-3 4e-3 --obs X Z --target-errors 400 --max-shots 400000 \
        --out campaign.json
    python ler_campaign.py --mode independent --plans ... --sinter-workers 12
    python ler_campaign.py --fit campaign.json          # fit + extrapolate
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import numpy as np
import scipy.sparse as sp

TASK = r"C:\Users\dtlic\Documents\GitHub\ShinkaEvolve\.claude\worktrees\infallible-gagarin-a88215\tasks\q70_ring_shuttle"
ROOT = os.path.dirname(os.path.dirname(TASK))
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, TASK, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evaluate as ev  # noqa: E402

# Paper Q70 reference, Pauli-only configuration (Table XII / certify.py header)
PAPER_ANSATZ = dict(alpha=1.07e6, beta=-3410.0, zeta=23.0, d_half=5)


def paper_ler_per_sec(p):
    a, b, z = PAPER_ANSATZ["alpha"], PAPER_ANSATZ["beta"], PAPER_ANSATZ["zeta"]
    return p ** PAPER_ANSATZ["d_half"] * math.exp(a * p * p + b * p + z)


# ---------------------------------------------------------------------------
# DEM -> (check matrix, observable matrix, priors)
# ---------------------------------------------------------------------------
def dem_matrices(dem):
    rows_d, cols_d, rows_o, cols_o, priors = [], [], [], [], []
    m = 0
    for inst in dem.flattened():
        if inst.type != "error":
            continue
        priors.append(inst.args_copy()[0])
        for t in inst.targets_copy():
            if t.is_relative_detector_id():
                rows_d.append(m)
                cols_d.append(t.val)
            elif t.is_logical_observable_id():
                rows_o.append(m)
                cols_o.append(t.val)
        m += 1
    H = sp.csc_matrix((np.ones(len(rows_d), np.uint8), (rows_d, cols_d)),
                      shape=(m, dem.num_detectors))
    L = sp.csc_matrix((np.ones(len(rows_o), np.uint8), (rows_o, cols_o)),
                      shape=(m, dem.num_observables))
    return H, L, np.asarray(priors, dtype=np.float64)


def sample_dem(priors, n, rng, chunk=2048):
    """EXACT independent Bernoulli draw over all mechanisms.

    Returns X (n x M) csr uint8, X[s, i] = 1 iff mechanism i fired in shot s.
    Done as a chunked dense threshold (chunk columns at a time) so the peak
    memory is n*chunk float32 rather than n*M; the cost is ~0.6 s per 4096-shot
    batch against ~600 s of decoding, i.e. negligible.
    """
    m = len(priors)
    rows, cols = [], []
    for lo in range(0, m, chunk):
        hi = min(lo + chunk, m)
        u = rng.random((n, hi - lo), dtype=np.float32)
        r, c = np.nonzero(u < priors[lo:hi].astype(np.float32))
        rows.append(r)
        cols.append(c + lo)
    rows = np.concatenate(rows) if rows else np.empty(0, int)
    cols = np.concatenate(cols) if cols else np.empty(0, int)
    return sp.csr_matrix((np.ones(len(rows), np.uint8), (rows, cols)),
                         shape=(n, m))


# ---------------------------------------------------------------------------
# decoders
# ---------------------------------------------------------------------------
def make_decoder(kind, dem, parallel=True):
    if kind == "relay-bp":
        from relay_bp.stim.sinter import SinterDecoder_RelayBP
        cd = SinterDecoder_RelayBP(parallel=parallel).compile_decoder_for_dem(
            dem=dem)
        nobs = dem.num_observables

        def run(dets):
            packed = np.packbits(dets.astype(np.uint8), axis=1,
                                 bitorder="little")
            out = cd.decode_shots_bit_packed(
                bit_packed_detection_event_data=packed)
            return np.unpackbits(out, axis=1, bitorder="little",
                                 count=nobs).astype(bool)
        return run
    if kind == "mem-bp":
        from relay_bp.stim.sinter import SinterDecoder_MemBP
        cd = SinterDecoder_MemBP(parallel=parallel).compile_decoder_for_dem(
            dem=dem)
        nobs = dem.num_observables

        def run(dets):
            packed = np.packbits(dets.astype(np.uint8), axis=1,
                                 bitorder="little")
            out = cd.decode_shots_bit_packed(
                bit_packed_detection_event_data=packed)
            return np.unpackbits(out, axis=1, bitorder="little",
                                 count=nobs).astype(bool)
        return run
    if kind == "bposd":
        from stimbposd import BPOSD
        d = BPOSD(dem, max_bp_iters=30, osd_order=0)
        return lambda dets: d.decode_batch(dets)
    raise ValueError(f"unknown decoder {kind}")


# ---------------------------------------------------------------------------
# checkpointing
# ---------------------------------------------------------------------------
def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def load(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def ratio_se(st, arm, ref, n):
    """Delta-method SE of R = mean(w*F_arm)/mean(F_ref) on PAIRED shots.

    The two arms see the same fault configurations, so Cov(Y, X) is large and
    positive and the ratio's error is far below the sqrt(2/N) of independent
    arms.  Y = w*F_arm, X = F_ref, both summed over the same n shots.
    """
    sy, sx = st["errors"][arm], st["errors"][ref]
    if sx <= 0:
        return float("nan"), float("nan")
    ybar, xbar = sy / n, sx / n
    vy = max(st["err_sq"][arm] / n - ybar ** 2, 0.0)
    vx = max(st["err_sq"][ref] / n - xbar ** 2, 0.0)
    cxy = st["cross"][arm] / n - ybar * xbar
    r = ybar / xbar
    var = (vy - 2 * r * cxy + r * r * vx) / (n * xbar * xbar)
    return r, math.sqrt(max(var, 0.0))


def wilson_ci(k, n, z=1.0):
    if n == 0:
        return (0.0, 1.0)
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


# ---------------------------------------------------------------------------
def run_paired(args, state):
    plans = {}
    for spec in args.plans:
        label, path = spec.split("=", 1)
        with open(path, encoding="utf-8") as f:
            plans[label] = ev.compile_plan(json.load(f))
        c = plans[label]
        print(f"[plan] {label:<8} transport={c['transport_rounds']:<4} "
              f"ms={c['merge_rounds']:<3} exposure={c['exposure']:.4f} "
              f"({path})", flush=True)
    labels = list(plans)
    ref = labels[0]
    rng = np.random.default_rng(args.seed)

    for p in args.p:
        for obs in args.obs:
            key = f"p={p:g}|obs={obs}"
            st = state["points"].setdefault(key, {
                "p": p, "obs": obs, "mode": "paired", "ref": ref,
                "shots": 0, "errors": {l: 0.0 for l in labels},
                "err_sq": {l: 0.0 for l in labels},
                "cross": {l: 0.0 for l in labels},
                "logw_sum": {l: 0.0 for l in labels},
                "logw_sq": {l: 0.0 for l in labels},
                "raw_fail": {l: 0 for l in labels},
                "decode_s": 0.0, "sample_s": 0.0})
            for k in ("cross", "logw_sum", "logw_sq"):
                st.setdefault(k, {l: 0.0 for l in labels})
            # sum over FAILING shots of w_a * w_b, for every ordered pair.
            # Needed to put a correct (paired) error bar on the DIFFERENCE of
            # two arms' ratios -- e.g. the real 300-round plan vs the padded
            # 300-round lever, which have identical exposure.  Treating those
            # two as independent overstates their difference's SE by ~10x,
            # because they see the same faults and their weights are almost
            # perfectly correlated.
            st.setdefault("pair", {a: {b: 0.0 for b in labels}
                                   for a in labels})
            if st["raw_fail"][ref] >= args.target_errors or \
                    st["shots"] >= args.max_shots:
                print(f"[skip] {key} already at "
                      f"{st['raw_fail'][ref]} errors / {st['shots']} shots",
                      flush=True)
                continue

            dems, weights = {}, {}
            t0 = time.time()
            for l in labels:
                circ = ev.build_circuit(plans[l], obs, p)
                dems[l] = circ.detector_error_model()
            # ONE fixed decoder for every arm.  Two reasons, both essential:
            #  (1) it is the protocol the comparison requires -- absolute LERs
            #      are not comparable across decoders, only a ratio under one
            #      decoder is;
            #  (2) relay-BP draws its relay gammas at random, so giving each
            #      arm its own decoder makes F_arm(x) != F_ref(x) for reasons
            #      that have nothing to do with the plans, which destroys the
            #      pairing (measured: 17 vs 11 raw failures on identical
            #      faults at p=4e-3).  With one decoder F is literally the
            #      same function of x and the ratio is the sample mean of w
            #      over failing shots.
            # `--decoder-dem` selects whose priors the decoder is built from;
            # run it once per arm to bound the prior-mismatch systematic.
            dem_for_decoder = dems[args.decoder_dem or ref]
            dec = make_decoder(args.decoder, dem_for_decoder,
                               parallel=bool(args.parallel))
            st["decoder_dem"] = args.decoder_dem or ref
            H, L, pr_ref = dem_matrices(dems[ref])
            m_ref = H.shape[0]
            for l in labels:
                if l == ref:
                    weights[l] = (np.zeros(m_ref), 0.0)
                    continue
                _H2, _L2, pr = dem_matrices(dems[l])
                if _H2.shape != H.shape or (_H2 != H).nnz or (_L2 != L).nnz:
                    raise SystemExit(
                        f"DEM supports differ between {ref} and {l}: the "
                        f"paired estimator is invalid; use --mode independent")
                if np.any(pr_ref <= 0) and np.any(pr > 0):
                    raise SystemExit("zero reference prior with nonzero arm "
                                     "prior: unbounded importance weight")
                c_i = np.log(pr / pr_ref) - np.log((1 - pr) / (1 - pr_ref))
                C = float(np.sum(np.log((1 - pr) / (1 - pr_ref))))
                weights[l] = (c_i, C)
            print(f"[setup] {key}  {time.time() - t0:.1f}s  "
                  f"mechanisms={m_ref}", flush=True)

            while st["raw_fail"][ref] < args.target_errors and \
                    st["shots"] < args.max_shots:
                n = int(min(args.batch, args.max_shots - st["shots"]))
                t0 = time.time()
                X = sample_dem(pr_ref, n, rng)
                dets = np.asarray((X @ H).todense()) % 2
                obsv = np.asarray((X @ L).todense()) % 2
                st["sample_s"] += time.time() - t0
                t0 = time.time()
                pred = dec(dets.astype(bool))
                st["decode_s"] += time.time() - t0
                fail = np.any(pred != obsv.astype(bool), axis=1)
                wf = {}
                for l in labels:
                    if l == ref:
                        wf[l] = np.ones(int(fail.sum()))
                    else:
                        c_i, C = weights[l]
                        wf[l] = np.exp(
                            np.asarray(X[fail] @ c_i).ravel() + C)
                for a in labels:
                    for b in labels:
                        st["pair"][a][b] += float((wf[a] * wf[b]).sum())
                for l in labels:
                    st["raw_fail"][l] += int(fail.sum())
                    if l == ref:
                        st["errors"][l] += float(fail.sum())
                        st["err_sq"][l] += float(fail.sum())
                        st["cross"][l] += float(fail.sum())
                    else:
                        c_i, C = weights[l]
                        logw = np.asarray(X @ c_i).ravel() + C
                        w = np.exp(logw)
                        st["errors"][l] += float(w[fail].sum())
                        st["err_sq"][l] += float((w[fail] ** 2).sum())
                        st["cross"][l] += float(w[fail].sum())
                        st["logw_sum"][l] += float(logw[fail].sum())
                        st["logw_sq"][l] += float((logw[fail] ** 2).sum())
                st["shots"] += n
                save(args.out, state)
                sh = st["shots"]
                msg = "  ".join(
                    f"{l}:{st['errors'][l]:.1f}" for l in labels)
                print(f"  [{key}] shots={sh:<8d} weighted_errors {msg}  "
                      f"({st['decode_s'] / sh * 1e3:.1f} ms/shot/arm-set)",
                      flush=True)
    return state


def run_independent(args, state):
    plans = {}
    for spec in args.plans:
        label, path = spec.split("=", 1)
        with open(path, encoding="utf-8") as f:
            plans[label] = ev.compile_plan(json.load(f))
        c = plans[label]
        print(f"[plan] {label:<8} transport={c['transport_rounds']:<4} "
              f"exposure={c['exposure']:.4f}", flush=True)
    rng = np.random.default_rng(args.seed)
    for label, comp in plans.items():
        for p in args.p:
            for obs in args.obs:
                key = f"{label}|p={p:g}|obs={obs}"
                st = state["points"].setdefault(key, {
                    "plan": label, "p": p, "obs": obs, "mode": "independent",
                    "shots": 0, "errors": 0, "decode_s": 0.0})
                if st["errors"] >= args.target_errors or \
                        st["shots"] >= args.max_shots:
                    print(f"[skip] {key}", flush=True)
                    continue
                circ = ev.build_circuit(comp, obs, p)
                dem = circ.detector_error_model()
                dec = make_decoder(args.decoder, dem, parallel=bool(args.parallel))
                sampler = circ.compile_detector_sampler(
                    seed=int(rng.integers(2 ** 31)))
                while st["errors"] < args.target_errors and \
                        st["shots"] < args.max_shots:
                    n = int(min(args.batch, args.max_shots - st["shots"]))
                    dets, obsv = sampler.sample(n, separate_observables=True)
                    t0 = time.time()
                    pred = dec(dets)
                    st["decode_s"] += time.time() - t0
                    st["errors"] += int(np.any(pred != obsv, axis=1).sum())
                    st["shots"] += n
                    save(args.out, state)
                    print(f"  [{key}] shots={st['shots']:<8d} "
                          f"errors={st['errors']:<6d} "
                          f"LER={st['errors'] / st['shots']:.3e} "
                          f"({st['decode_s'] / st['shots'] * 1e3:.1f} ms/shot)",
                          flush=True)
    return state


# ---------------------------------------------------------------------------
def report(state):
    print("\n" + "=" * 100)
    pts = state["points"]
    if not pts:
        return
    any_paired = any(v.get("mode") == "paired" for v in pts.values())
    if any_paired:
        print(f"{'point':<20} {'arm':<8} {'shots':>9} {'w.errors':>10} "
              f"{'LER/shot':>11} {'LER/SEC':>11} {'ratio vs ref':>16}")
        for key, st in pts.items():
            ref = st["ref"]
            n = st["shots"]
            if n == 0:
                continue
            for l, e in st["errors"].items():
                ler = e / n
                sec = 1 - (1 - ler) ** (1 / ev.NC_SECS) if 0 < ler < 1 else ler
                if l == ref:
                    lo, hi = wilson_ci(int(e), n)
                    rat = "  (reference)"
                    ci = (f"[{lo:.3e},{hi:.3e}]  "
                          f"raw_fail={st['raw_fail'][l]}")
                else:
                    r, se_r = ratio_se(st, l, ref, n)
                    rat = f"{r:.4f}"
                    nsig = (r - 1) / se_r if se_r > 0 else float("nan")
                    sdlw = 0.0
                    k = st["raw_fail"][l]
                    if k > 1:
                        mu = st["logw_sum"][l] / k
                        sdlw = math.sqrt(max(st["logw_sq"][l] / k - mu * mu, 0))
                    ci = (f"+-{se_r:.4f} ({nsig:+.1f} sigma from 1.0)  "
                          f"sd(log w|F)={sdlw:.4f}")
                print(f"{key:<20} {l:<8} {n:>9d} {e:>10.1f} {ler:>11.4e} "
                      f"{sec:>11.4e} {rat:>16} {ci}")
    else:
        print(f"{'point':<32} {'shots':>9} {'errors':>8} {'LER/shot':>12} "
              f"{'LER/SEC':>12}  68% CI")
        for key, st in pts.items():
            n, e = st["shots"], st["errors"]
            if n == 0:
                continue
            ler = e / n
            sec = 1 - (1 - ler) ** (1 / ev.NC_SECS) if 0 < ler < 1 else ler
            lo, hi = wilson_ci(e, n)
            print(f"{key:<32} {n:>9d} {e:>8d} {ler:>12.4e} {sec:>12.4e}  "
                  f"[{lo:.3e}, {hi:.3e}]")
    print(f"\npaper Q70 reference (Table XII, Pauli-only, BEAM-SEARCH decoder "
          f"-- NOT comparable in absolute terms):")
    for p in (1e-3, 5e-4, 1e-4):
        print(f"   p={p:g}  LER/SEC {paper_ler_per_sec(p):.3e}")


def fit_and_extrapolate(path, targets=(1e-3, 1e-4)):
    """Fit LER/SEC = p^5 exp(a p^2 + b p + z) to each arm and extrapolate."""
    state = load(path)
    if state is None:
        raise SystemExit(f"no such campaign file: {path}")
    series = {}
    for key, st in state["points"].items():
        n = st["shots"]
        if n == 0:
            continue
        if st.get("mode") == "paired":
            for l, e in st["errors"].items():
                if e <= 0:
                    continue
                ler = e / n
                sec = 1 - (1 - ler) ** (1 / ev.NC_SECS)
                series.setdefault(f"{l}|{st['obs']}", []).append(
                    (st["p"], sec, math.sqrt(max(e, 1.0)) / n))
        else:
            if st["errors"] <= 0:
                continue
            ler = st["errors"] / n
            sec = 1 - (1 - ler) ** (1 / ev.NC_SECS)
            series.setdefault(f"{st['plan']}|{st['obs']}", []).append(
                (st["p"], sec, math.sqrt(st["errors"]) / n))
    print(f"{'series':<16} {'n_pts':>5}  fitted (alpha, beta, zeta)"
          + "".join(f"   LER/SEC@{t:g}" for t in targets))
    out = {}
    for name, pts in sorted(series.items()):
        pts.sort()
        if len(pts) < 3:
            print(f"{name:<16} {len(pts):>5}  (need >=3 points to fit)")
            continue
        P = np.array([q[0] for q in pts])
        Y = np.log(np.array([q[1] for q in pts])) - 5 * np.log(P)
        W = 1.0 / np.maximum([q[2] / q[1] for q in pts], 1e-6)
        A = np.vstack([P ** 2, P, np.ones_like(P)]).T
        coef, *_ = np.linalg.lstsq(A * W[:, None], Y * W, rcond=None)
        a, b, z = coef
        ex = [t ** 5 * math.exp(a * t * t + b * t + z) for t in targets]
        out[name] = {"alpha": a, "beta": b, "zeta": z,
                     "extrap": dict(zip(map(str, targets), ex))}
        print(f"{name:<16} {len(pts):>5}  ({a:.4g}, {b:.4g}, {z:.4g})"
              + "".join(f"   {e:.4e}" for e in ex))
    print(f"\npaper published:  (1.07e6, -3410, 23.0)"
          + "".join(f"   {paper_ler_per_sec(t):.4e}" for t in targets))
    return out


def validate_sampler(args):
    """DEM-sampled detector/observable statistics vs stim's circuit sampler."""
    label, path = args.plans[0].split("=", 1)
    with open(path, encoding="utf-8") as f:
        comp = ev.compile_plan(json.load(f))
    p = args.p[0]
    circ = ev.build_circuit(comp, args.obs[0], p)
    dem = circ.detector_error_model()
    H, L, pr = dem_matrices(dem)
    rng = np.random.default_rng(7)
    n = 20000
    X = sample_dem(pr, n, rng)
    d1 = np.asarray((X @ H).todense()) % 2
    o1 = np.asarray((X @ L).todense()) % 2
    d2, o2 = circ.compile_detector_sampler(seed=7).sample(
        n, separate_observables=True)
    # per-shot detector fraction: SEM across shots, so detector correlations
    # inside a shot are handled correctly (a naive n*D binomial sigma is far
    # too small).
    f1, f2 = d1.mean(axis=1), d2.mean(axis=1)
    se = math.sqrt(f1.var(ddof=1) / n + f2.var(ddof=1) / n)
    print(f"DEM-sampled   detector rate {f1.mean():.6f}  "
          f"obs-flip rate {o1.mean():.6f}")
    print(f"stim-sampled  detector rate {f2.mean():.6f}  "
          f"obs-flip rate {o2.mean():.6f}")
    print(f"detector-rate difference {abs(f1.mean() - f2.mean()):.2e} "
          f"(1 sigma = {se:.2e} -> {abs(f1.mean() - f2.mean()) / se:.2f} sigma)")
    # per-detector agreement (the sharper test)
    pd1, pd2 = d1.mean(axis=0), d2.mean(axis=0)
    z = (pd1 - pd2) / np.sqrt(np.maximum(pd1 * (1 - pd1) + pd2 * (1 - pd2), 1e-12) / n)
    print(f"per-detector z-scores: max|z| = {np.abs(z).max():.2f}, "
          f"mean z = {z.mean():+.3f}, sd z = {z.std():.3f} "
          f"(expect ~N(0,1) over {len(z)} detectors)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plans", nargs="+",
                    default=["ours=ours_244.json",
                             "paper=paper_equiv_424.json"],
                    help="label=path.json ; the FIRST is the paired reference")
    ap.add_argument("--p", nargs="+", type=float, default=[6e-3, 5e-3, 4e-3])
    ap.add_argument("--obs", nargs="+", default=["X", "Z"])
    ap.add_argument("--mode", choices=("paired", "independent"),
                    default="paired")
    ap.add_argument("--decoder", default="relay-bp",
                    choices=("relay-bp", "mem-bp", "bposd"))
    ap.add_argument("--decoder-dem", default=None,
                    help="paired mode: which arm's DEM the single fixed "
                         "decoder is built from (default: the reference arm). "
                         "Run once per arm to bound the prior-mismatch "
                         "systematic.")
    ap.add_argument("--target-errors", type=int, default=400)
    ap.add_argument("--max-shots", type=int, default=400_000)
    ap.add_argument("--batch", type=int, default=4096)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--seed", type=int, default=20260730)
    ap.add_argument("--out", default="campaign.json")
    ap.add_argument("--fit", default=None,
                    help="fit+extrapolate an existing campaign file and exit")
    ap.add_argument("--validate-sampler", action="store_true")
    args = ap.parse_args()

    if args.fit:
        fit_and_extrapolate(args.fit)
        return
    if args.validate_sampler:
        validate_sampler(args)
        return

    state = load(args.out) or {"points": {}, "meta": {}}
    state["meta"].update({"mode": args.mode, "decoder": args.decoder,
                          "plans": args.plans, "p": args.p, "obs": args.obs,
                          "target_errors": args.target_errors,
                          "max_shots": args.max_shots,
                          "nc_secs": ev.NC_SECS,
                          "started": state["meta"].get(
                              "started", time.strftime("%Y-%m-%d %H:%M:%S"))})
    t0 = time.time()
    state = (run_paired if args.mode == "paired" else run_independent)(
        args, state)
    state["meta"]["wall_s"] = state["meta"].get("wall_s", 0.0) + time.time() - t0
    save(args.out, state)
    report(state)
    print(f"\nwall clock this session: {(time.time() - t0) / 3600:.2f} h "
          f"-> {args.out}")


if __name__ == "__main__":
    main()
