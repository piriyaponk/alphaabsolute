# AlphaAbsolute — 14-Theme Bottleneck Framework Knowledge Base
**Built:** 2026-05-14 | **Agent:** 05 Thematic Research + 16 Auditor verified
**Sources:** IEA, TrendForce, Goldman Sachs, SemiAnalysis, TSMC filings, MarketsandMarkets, SpaceNews, FAA filings, institutional research

---

## Theme 1: AI-Related (NVDA, MSFT, PLTR, SOUN)

**Value Chain:**
Foundation models / AI software → AI accelerators (GPU/ASIC) → Advanced packaging (CoWoS) → HBM memory stacks → Server OEM (rack systems) → Data center build-out → Cloud delivery → End applications (enterprise AI, agents, voice AI)

**THE BOTTLENECK:** Two concurrent bottlenecks:
1. **TSMC CoWoS advanced packaging** — NVIDIA controls ~60% of TSMC's total CoWoS-L output through 2027. CoWoS-L capacity is fully booked; TSMC's new AP6 fab for CoWoS-L not online until 2028. Lead times measured in quarters.
2. **Data center power / grid interconnect** — 7 GW shortfall in 2026. 18–36 month grid interconnect timelines. Transformer lead times 24–60 months. Microsoft CEO confirmed "GPU inventory can't be plugged in" due to power constraints.

**Bottleneck Owners (listed):**
- **NVDA** — GPU monopoly: 85%+ AI accelerator market share; $197B data center revenue (FY2026). Controls CoWoS allocation. Pricing power = EXTREME (no substitute at scale).
- **MSFT / GOOGL / AMZN / META** — hyperscaler demand; collectively $650–690B capex in 2026. Not bottleneck owners but demand anchors.
- **PLTR** — AI software operating layer for defense/enterprise; $10B Army contract; $7.2B 2026 revenue guidance (+61% YoY). AIP platform = moat via data network effect.
- **SOUN** — Voice AI / conversational AI; $100M+ ARR run-rate; niche bottleneck in enterprise voice stack.

**Demand Drivers:**
1. Hyperscaler AI capex race: $650–720B combined 2026 (Alphabet $175–185B; Amazon ~$200B; Meta $115–135B)
2. Enterprise AI adoption: 1.1 billion active AI users globally in 2026; enterprise AIP bootcamps expanding
3. Inference scaling: inference workloads growing faster than training; requires sustained GPU demand even after training clusters filled

**Adoption Phase (2026):** GROWTH — accelerating, not yet plateau

**NRGC Phase signal:** Phase 3–4 — Institutional Discovery → Narrative Expansion. Consensus formed around NVDA. PLTR entering Phase 3 (institutional discovery). SOUN Phase 2 (early acceleration). Watch for Phase 5 signals in NVDA (RSI, climax volume).

**Bear Case:** Custom ASICs (Google TPU, Amazon Trainium, Microsoft Maia) displace NVDA market share faster than expected. CoWoS bottleneck eases faster than priced (TSMC capacity surge). Energy costs make inference economics negative. Regulatory AI restrictions.

**Key Monitoring Signals:**
- TSMC CoWoS capacity utilization (quarterly earnings)
- Hyperscaler capex revisions (quarterly — any guidance cut = sell signal)
- NVDA data center revenue YoY growth rate (deceleration flag)
- Grid interconnect queue data (US DOE monthly)
- PLTR U.S. commercial revenue growth (quarterly — must maintain 100%+ YoY)

---

## Theme 2: Memory / HBM (MU, WDC, AMAT, SK Hynix)

**Value Chain:**
Silicon wafer → DRAM fab (HBM process: ~2x wafer area vs standard DRAM) → Through-Silicon Via (TSV) stacking → HBM die stack (8–12 layers) → CoWoS integration with GPU die → System-level validation → Hyperscaler deployment

**THE BOTTLENECK:** HBM supply is structurally constrained through 2027+:
- HBM now consumes **23% of total DRAM wafer output** (up from 19% in 2025 per TrendForce), cannibalizing standard DRAM supply
- Production yields at 50–60% for advanced HBM3E; each AI chip requires 3–4x the wafer equivalent of standard DRAM
- All three suppliers (SK Hynix, Samsung, Micron) sold out through 2026; customers pre-committing years in advance
- SK Hynix warned wafer shortage could last until 2030
- HBM4 mass production started February 2026 (for NVIDIA Rubin architecture)

**Bottleneck Owners (listed):**
- **SK Hynix** (unlisted in US; Korea: 000660) — 62% HBM market share; NVIDIA's #1 supplier; pricing power = EXTREME. Plans to hike HBM3E prices ~20% for 2026. M15X fab online 2027.
- **MU (Micron Technology)** — ~13% HBM share but fastest ramp; only US-listed pure HBM play; HBM3E production ramping; PRICING POWER = HIGH. Revenue inflection 2025–2026.
- **WDC (Western Digital)** — NAND/HDD; less direct HBM exposure; benefits from storage demand in AI data centers. Pricing power = MODERATE.
- **AMAT (Applied Materials)** — etch and deposition equipment for both HBM fab and standard DRAM; sell-the-picks-not-the-miner play. Pricing power = HIGH (duopoly with LRCX).

**Demand Drivers:**
1. AI accelerator demand: every NVIDIA H200/B200 requires 6–8 HBM3E stacks; Rubin requires HBM4
2. Supply structural tightening: 40% of advanced wafer capacity redirected to HBM by SK Hynix and Samsung
3. Price supercycle: HBM3E ASP up 20% in 2026; broader DRAM contract prices surged ~420% in 2025; HBM market $35B in 2025 → $100B by 2028 (40% CAGR)

**Adoption Phase (2026):** GROWTH — HBM in full supercycle; not yet mature

**NRGC Phase signal:** Phase 3 — Institutional Discovery. MU specifically: Phase 2→3 (first institutional recognition, EPS acceleration phase underway). SK Hynix Phase 3–4 (narrative expansion, but Korean market).

**Bear Case:**
- Samsung yields on HBM4 improve sharply → market share shift, price pressure
- Micron catches up faster → supply glut by late 2027
- AI capex slowdown reduces GPU demand → HBM demand drop
- New packaging technology bypasses HBM stacking requirement

**Key Monitoring Signals:**
- TrendForce HBM wafer allocation % monthly
- SK Hynix / Samsung earnings: HBM ASP and yield commentary
- MU quarterly: HBM revenue as % of total + margin profile
- NVIDIA HBM per-chip spec for next architecture (Rubin → next)
- DRAM spot price (early leading indicator)

---

## Theme 3: Space (RKLB, LUNR, AST, ASTS)

**Value Chain:**
Launch vehicles → Satellite bus manufacturing → Payload/sensor integration → Ground station network → Data downlink/processing → End applications (imaging, comms, GPS, IoT, defense ISR)

**THE BOTTLENECK:** Two-tier bottleneck:
1. **Launch capacity** — SpaceX commands ~70%+ of global launch market share (130+ launches in 2025). Wait times for Falcon 9 commercial slots: 12–24 months. RKLB Neutron (medium lift, 13-ton reusable) delayed to Q4 2026 minimum (propellant tank failure in testing). The medium-lift bottleneck is acute: "practical monopoly" per Rocket Lab.
2. **Satellite manufacturing throughput** — demand for smallsats exceeds manufacturing capacity; specialized RF/optical components (phased arrays, high-gain antennas) in short supply.

**Cost context:** Falcon 9: ~$2,720/kg to LEO. Rocket Lab Electron: ~$22,000/kg (small-sat niche). Starship target: $13–32/kg at full reusability (20–70 flights). Starship changes economics of everything above 10 tons.

**Bottleneck Owners (listed):**
- **RKLB (Rocket Lab)** — Electron dominates small-sat dedicated launch; $2.2B backlog including 70+ missions; Neutron debut Q4 2026. Space Systems division (satellite buses) = growing recurring revenue. Pricing power = HIGH in dedicated smallsat launch.
- **LUNR (Intuitive Machines)** — Lunar logistics monopoly; NASA CLPS contract holder; only commercial company with successful lunar landing. Pricing power = HIGH (no competition in cislunar).
- **ASTS (AST SpaceMobile)** — Direct-to-device satellite broadband; 6 BlueBird sats launched; targeting 45–60 sats by end 2026; $1B committed revenue from 50+ MNOs; nationwide intermittent service available 2026. Bottleneck = launch cadence and capital.

**Demand Drivers:**
1. Defense ISR: DoD accelerating satellite procurement post-Ukraine war lessons; JADC2 requirement for ubiquitous space-based sensing
2. Direct-to-device connectivity: Starlink, ASTS, and Amazon Kuiper competing for global broadband; ~3B underserved subscribers
3. Commercial Earth observation: AI-powered analytics driving demand for daily revisit imagery; Planet Labs, BlackSky, etc.

**Adoption Phase (2026):** EARLY-GROWTH — launch infrastructure scaling; satellite services entering commercial phase

**NRGC Phase signal:** RKLB Phase 2–3 (revenue acceleration + institutional recognition). LUNR Phase 2 (early acceleration, post first Moon landing). ASTS Phase 2 (revenue just beginning; commercial service commencing 2026).

**Bear Case:**
- Starship achieves full reusability → destroys pricing for all other launch providers
- ASTS BlueBird satellite failure rate / launch delays push timelines to 2027–2028
- LUNR loses NASA contract renewal
- Geopolitical restrictions on satellite data (China/Russia tension)

**Key Monitoring Signals:**
- RKLB Neutron test milestones (propellant tank fix + static fire)
- ASTS satellite launch cadence and in-orbit performance (connection bandwidth tests)
- SpaceX Starship reusability milestone (catch attempts vs. launch count)
- LUNR NASA CLPS contract pipeline announcements
- Global satellite launch manifest (Payload Space / SpaceNews monthly)

---

## Theme 4: Quantum Computing (IONQ, RGTI, QUBT, IBM)

**Value Chain:**
Qubit hardware (trapped ion / superconducting / photonic / neutral atom) → Error correction layer → Quantum middleware / compilers → Cloud-delivered quantum access → Hybrid classical-quantum algorithms → End applications (chemistry, logistics, finance, ML)

**THE BOTTLENECK:** Not supply — it's TECHNOLOGY:
- Physical error rates remain 1,000–10,000x too high for fault-tolerant computation at scale
- Logical qubit count bottleneck: IonQ targeting 80,000 logical qubits by 2030; IBM targeting 200 logical qubits by 2029 (Starling/Kookaburra)
- Coherence time and gate fidelity: IonQ achieved 99.99% two-qubit gate fidelity (2025 world record); IBM Kookaburra (4,158 physical qubits, 2026) = first QEC-enabled module
- Cryogenic cooling infrastructure for superconducting qubits: limits to room-scale deployment
- Quantum-classical interface bandwidth: classical computers can't feed quantum processors fast enough at scale

**Market Size:** Global quantum revenues $650–750M in 2024; crossing $1B threshold in 2025; full commercial advantage estimated 2028–2033 timeframe (IBM targets fault-tolerance by 2029).

**Bottleneck Owners (listed):**
- **IONQ** — Trapped-ion leader; 99.99% gate fidelity; 2030 target: 2M physical qubits / 80K logical. First documented quantum advantage over classical HPC in medical device simulation (2025). Pricing power = MODERATE (still pre-revenue scale, $43M 2025 revenue).
- **RGTI (Rigetti)** — Superconducting qubits; faster gate speeds than trapped ion but lower fidelity; cloud delivery via AWS/Azure. Pricing power = LOW-MODERATE.
- **QUBT (Quantum Computing Inc.)** — Photonic quantum sensing + reservoir computing; earlier commercial focus. Pricing power = LOW.
- **IBM** — Kookaburra system (4,158 qubits) targeting 2026; roadmap to Starling (200 logical qubits, 2029). IBM's quantum-safe cryptography (post-quantum) is nearer-term commercial play. Pricing power = HIGH (enterprise relationships).

**Demand Drivers:**
1. Post-quantum cryptography urgency: NIST standards published 2024; governments mandating transition by 2030 → IBM quantum-safe = near-term revenue
2. Pharmaceutical / materials simulation: quantum chemistry is the "killer app"; $150B+ pharma R&D spending that quantum could transform
3. AI optimization convergence: hybrid quantum-classical for logistics routing, portfolio optimization

**Adoption Phase (2026):** EARLY — technology not yet commercial-scale; current = "quantum utility" demonstrations not "quantum advantage" at scale

**NRGC Phase signal:** Phase 1–2 — Neglect/Early Acceleration. Revenue inflecting but narrative > reality currently. High speculation risk. IONQ is the most advanced commercially. IBM is the safest play (hidden within large-cap).

**Bear Case:**
- Classical computing (AI + HPC) solves problems quantum was supposed to address first
- Error correction timeline slips further (happens every year historically)
- Chinese quantum programs (undisclosed breakthroughs) create geopolitical risk
- Funding dries up for pre-revenue pure-plays (RGTI, QUBT at risk)

**Key Monitoring Signals:**
- IonQ algorithmic qubit (AQ) benchmarks (quarterly)
- IBM Kookaburra deployment timeline and QEC metrics
- Government contracts: DARPA/NIST quantum procurement
- Revenue trajectory: IONQ quarterly bookings and contract announcements
- Error rate milestones: <1E-6 logical error rate = major inflection signal

---

## Theme 5: Photonics / Optical Interconnect (LITE, COHR, FNSR/IIVI)

**Value Chain:**
InP / GaAs wafer production → EML laser chip fabrication → Optical component assembly (transceivers, modulators) → Module integration (pluggable → LPO → CPO) → Switch ASIC integration → Data center fabric deployment → Network scaling

**THE BOTTLENECK:** EML (Electro-absorption Modulated Laser) chip supply:
- EML laser chips are **40–60% undersupplied through 2027** (McKinsey estimate)
- InP (Indium Phosphide) wafer capacity near physical limits; adding capacity takes 3–5 years
- NVIDIA pre-allocated laser chip capacity at major suppliers; invested $4B combined in Lumentum (LITE) and Coherent (COHR) in March 2026 specifically to secure priority access
- 800G transceiver shipments growing 100% YoY; 1.6T entering production; 3.2T in development — all generations ramping simultaneously (compressed cycle)
- CPO (Co-Packaged Optics) market: $95M in 2025 → $1.05B by 2034 (30.6% CAGR); NVIDIA deploying CPO in Quantum-X switches (early 2026) and Spectrum-X (H2 2026)

**Market Size:** Optical Interconnect in AI Data Centers: $3.75B in 2025 → $18.36B by 2033 (21.87% CAGR)

**Bottleneck Owners (listed):**
- **LITE (Lumentum)** — EML laser chips + ROADM for telecom/datacom; key CPO component supplier; NVIDIA strategic partner. Pricing power = VERY HIGH (InP wafer supply constraint).
- **COHR (Coherent)** — Largest vertically integrated optical components company post-II-VI merger; covers transceivers, lasers, fibers; NVIDIA partner. Pricing power = HIGH.
- **IIVI (now merged into COHR)** — Integration complete; compound semiconductor expertise (SiC, InP, GaAs). Pricing power = HIGH within COHR.

**Demand Drivers:**
1. GPU cluster scaling: 100,000-GPU clusters require all-optical interconnect; electrical interconnects hit physical bandwidth limits at >800G
2. CPO adoption mandate: NVIDIA making CPO mandatory for next-gen switches (3.5x power reduction, 10x resiliency); every new switch = optical demand
3. AI inference scaling: inference clusters need high-bandwidth, low-latency interconnects; data center count multiplying globally

**Adoption Phase (2026):** GROWTH — 800G mainstream, 1.6T entering production, CPO transitioning from pilot to volume

**NRGC Phase signal:** Phase 2–3 — Early Acceleration → Institutional Discovery. LITE and COHR still below peak consensus recognition. EML supply constraint not widely modeled. NVIDIA's $4B investment is the institutional discovery signal.

**Bear Case:**
- Silicon photonics matures faster (cheaper, CMOS-compatible) → displaces InP/GaAs compound semiconductor advantage
- Hyperscaler capex cuts reduce transceiver demand
- Chinese optical competitors (Accelink, HG Genuine) gain market share via pricing
- CPO adoption slower than expected (thermal management challenges in co-packaging)

**Key Monitoring Signals:**
- 800G/1.6T transceiver shipment volumes (quarterly earnings)
- InP wafer spot pricing (leading indicator)
- NVIDIA CPO deployment status (Quantum-X and Spectrum-X rollout)
- LITE and COHR order backlog and gross margin (price vs. cost dynamic)
- TSMC silicon photonics capacity announcements

---

## Theme 6: DefenseTech (PLTR, CACI, LDOS, AXON)

**Value Chain:**
Requirements definition (DoD/NATO) → AI/software development → System integration → Deployment (tactical edge/cloud) → Ongoing SaaS/sustainment → Upgrade cycles → Export/allied sales

**THE BOTTLENECK:** Cleared developer talent + classified network access:
- Security clearance processing takes 12–24 months; cleared AI/ML engineers are the scarce resource
- ITAR-compliant software stacks limit who can build; only a handful of primes have the infrastructure
- DoD's JWCC (Joint Warfighter Cloud Capability) and CJADC2 frameworks create winner-take-most dynamics — whoever gets the platform contract owns the data layer
- $13.4B DoD AI/autonomy budget for FY2026 (largest ever); $9.8B for autonomous/unmanned systems; $66B total IT budget
- FY2027 proposed defense budget: $1.5 trillion

**Bottleneck Owners (listed):**
- **PLTR (Palantir)** — AI operating system for defense (Gotham) + enterprise (AIP); $10B Army enterprise agreement (10 years); Q1 2026 revenue $1.63B (+85% YoY); U.S. government revenue +84% YoY. Pricing power = EXTREME (switching cost: data gravity moat, mission-critical systems).
- **CACI International** — Intelligence and cyber; cleared workforce of 22,000+; prime on NSA/NGA contracts. Pricing power = HIGH.
- **LDOS (Leidos)** — Largest pure-play defense IT; health IT + DoD; $15B+ backlog. Pricing power = HIGH.
- **AXON Enterprise** — Law enforcement tech (Taser, body cams, AI evidence platform); effectively a defense-adjacent monopoly in police tech; expanding into federal. Pricing power = VERY HIGH (Taser + Evidence.com data lock-in).

**Demand Drivers:**
1. Ukraine war lessons: AI-enabled targeting, drone swarms, and ISR demonstrated decisive advantage → NATO members accelerating AI defense spending
2. DOGE-driven efficiency mandates: DoD $66B IT budget must show AI efficiency gains; PLTR AIP is the platform of choice
3. Law enforcement modernization: AXON's AI-powered evidence and body camera ecosystem penetrating all major US metro departments

**Adoption Phase (2026):** GROWTH — software/AI defense entering rapid adoption after years of procurement reform

**NRGC Phase signal:** PLTR Phase 3–4 (Institutional Discovery → Narrative Expansion; 148% 2025 gain; now consensus). CACI/LDOS Phase 2–3. AXON Phase 3 (dominant franchise, steady compounder).

**Bear Case:**
- Budget reconciliation or DOGE cuts reduce DoD IT spend (unlikely but tail risk)
- PLTR valuation (75x+ forward revenue) requires perfection; any revenue miss = severe multiple compression
- Prime contractors (RTX, LMT, BA) build competing AI layers
- AXON regulatory risk: AI policing backlash, privacy legislation

**Key Monitoring Signals:**
- DoD contract announcements (USASpending.gov weekly)
- PLTR U.S. commercial revenue growth (must hold 100%+ YoY)
- Congress defense budget progression (continuing resolution risk)
- AXON body camera and Taser unit volume + Evidence.com ARR
- Cleared job openings (leading indicator of prime contractor expansion)

---

## Theme 7: Data Center (EQIX, DLR, VRT, ETN)

**Value Chain:**
Land acquisition + power securing → Structural/civil construction → Power infrastructure (substation, UPS, generators) → Cooling (CRAC → liquid cooling) → IT white space build-out → Network connectivity fabric → Hyperscaler/enterprise tenant deployment → Ongoing facilities management

**THE BOTTLENECK:** Power and grid interconnect is now THE single constraint:
- US data center power demand: 61.8 GW in 2025 → **75.8 GW in 2026** (S&P Global) → ~945 TWh globally by 2030 (IEA)
- Northern Virginia (Loudoun County) vacancy <5%; Dominion Energy moratorium on new large connections
- Transformer lead times: 24–30 months (standard) → **3–5 years** for large power transformers
- >50% of US data centers planned for 2026 delayed/canceled due to transformer and electrical equipment shortage (Bloomberg/April 2026)
- 12 GW announced for 2026 completion; only 5 GW under active construction → **7 GW shortfall**

**Bottleneck Owners (listed):**
- **EQIX (Equinix)** — 260+ data centers in 70+ metros; colocation + interconnection fabric; Q1 2026 revenue $2.44B (+9.8%); 60% of largest deals AI-related; 8 of top 10 AI model providers expanding; AFFO guidance $4.20–4.28B 2026. Pricing power = HIGH (interconnection = switching cost moat).
- **DLR (Digital Realty)** — Hyperscale-focused REIT; 300+ facilities; 2026 FFO $7.90–8.00/share; revenue ~$6.6B. Pricing power = MODERATE-HIGH (longer lease durations).
- **VRT (Vertiv)** — Power + cooling infrastructure; Q4 2025 orders +252%; $15B backlog (+109%); liquid cooling revenue doubled; CAGR 40% through 2028. Pricing power = VERY HIGH (sole-source for many liquid cooling specs).
- **ETN (Eaton)** — Electrical switchgear + UPS + transformers; Electrical Americas Q4 sales $3.51B (+21%); $1.2B capacity expansion; leading transformer supplier. Pricing power = HIGH.

**Demand Drivers:**
1. AI training/inference clusters: 100kW+ per rack (vs. 10–15kW traditional) → 10x power intensity increase driving power infrastructure spend
2. Hyperscaler capex: $650–720B combined 2026; ~75% directly tied to AI infrastructure (~$450–540B)
3. Colocation demand: US colocation market $46.84B in 2026 (+16.5% YoY); AI developers taking 60%+ of new leases at EQIX

**Adoption Phase (2026):** GROWTH — power infrastructure build-out is a multi-year cycle at full speed

**NRGC Phase signal:** VRT Phase 3–4 (Institutional Discovery → Narrative Expansion; orders backlog confirms). EQIX/DLR Phase 3 (stable compounder accelerating). ETN Phase 3 (power cycle supercycle beginning).

**Bear Case:**
- Energy crisis creates regulatory caps on data center power draws
- Hyperscaler shift to on-premise builds (away from colo) pressures EQIX/DLR
- Liquid cooling cost overruns slow VRT margin expansion
- Interest rate spike re-rates REIT multiples (DLR/EQIX are rate-sensitive)

**Key Monitoring Signals:**
- VRT order backlog quarterly (leading 12-18 month demand indicator)
- EQIX xScale (hyperscale) bookings and power MW contracted
- US grid interconnection queue data (FERC quarterly)
- Transformer lead time reports (industry publications monthly)
- Utility capex plans for new substation capacity (Duke, Dominion, Georgia Power)

---

## Theme 8: Nuclear / SMR (NNE, OKLO, CEG, CCJ)

**Value Chain:**
Uranium mining + conversion + enrichment → Fuel fabrication → Reactor design/NRC licensing → Construction (traditional) OR factory manufacturing (SMR) → Grid connection → Power Purchase Agreement → Revenue generation

**THE BOTTLENECK:** Two distinct bottlenecks:
1. **Regulatory / licensing**: NRC design certification takes 7–10 years; NuScale is the ONLY SMR with full NRC Design Approval (2023). Next SMRs (Oklo, X-energy) targeting 2028–2030+ first operation. Even the best timeline: power by 2030 for first-of-a-kind units.
2. **Uranium enrichment capacity**: Most US reactors require Low-Enriched Uranium (LEU) or High-Assay Low-Enriched Uranium (HALEU) for advanced designs; HALEU enrichment capacity in US near-zero (Centrus/USEC restarting); historically dependent on Russia (now embargoed).

**Market context:**
- AI data centers to quadruple power demand to 1,600 TWh by 2034
- Oklo pipeline: 14+ GW of signed agreements (Meta 1.2 GW Ohio, 2030 target); Oklo revenue = $0 (2025 loss: $139M)
- NNE: pre-revenue; Q1 2026 net loss $6.52M
- SMR market: $6.9B (2025) → $13.8B (2032)
- CEG (Constellation Energy): existing nuclear fleet (22 GW operating capacity); real revenue now; Three Mile Island Unit 1 restarted for Microsoft

**Bottleneck Owners (listed):**
- **CEG (Constellation Energy)** — Only publicly listed, fully operational nuclear utility at scale; 22 GW fleet; real revenue; Microsoft/TMI deal is the template. Pricing power = VERY HIGH (carbon-free baseload = premium pricing in AI PPA market).
- **CCJ (Cameco)** — World's largest publicly traded uranium producer; Cigar Lake + McArthur River mines; uranium spot price beneficiary. Pricing power = HIGH (oligopoly supply).
- **OKLO** — Pre-revenue Aurora microreactor (1.5–15 MW); Oklo's 75 MWe upsize design; 14 GW pipeline; first power 2030 at earliest. Pricing power = SPECULATIVE (licensing not complete).
- **NNE (Nano Nuclear Energy)** — Micro-reactor developer; ZEUS and ODIN designs; pre-revenue; R&D stage. Pricing power = SPECULATIVE.

**Demand Drivers:**
1. AI data center 24/7 carbon-free power demand: hyperscalers' PPAs require always-on clean power (solar/wind don't qualify for baseload); nuclear is the only solution
2. Grid decarbonization mandates: EPA regulations driving coal retirement → nuclear fills gap
3. HALEU supply security: geopolitical drive to reshore enrichment → domestic enrichment companies (Centrus) and mining (CCJ) benefit

**Adoption Phase (2026):** EARLY for SMR (licensing phase); GROWTH for existing nuclear (CEG, CCJ)

**NRGC Phase signal:** CEG/CCJ Phase 3 (Institutional Discovery — real revenue, narrative confirmed). OKLO/NNE Phase 1–2 (Neglect/Early Acceleration; pre-revenue, high narrative risk). Do NOT size OKLO/NNE as if Phase 3.

**Bear Case:**
- SMR construction costs balloon (nuclear construction has 3–5x historical cost overruns)
- NRC approval timelines extend further
- Natural gas stays cheap (displaces nuclear economics)
- Hyperscalers sign gas PPAs instead of waiting for SMR timelines
- Uranium price drops if enrichment capacity builds faster than expected

**Key Monitoring Signals:**
- NRC licensing milestones for Oklo, X-energy, TerraPower
- CEG PPA announcements (hyperscaler nuclear deals = price re-rating catalyst)
- CCJ uranium production guidance and spot/contract price differential
- HALEU enrichment capacity additions (Centrus quarterly)
- Utility interconnection agreements for SMR sites

---

## Theme 9: NeoCloud (CRWV, SMCI, NTAP, CORZ)

**Value Chain:**
Power contracts (grid/generator) → GPU procurement (NVIDIA primary) → Server rack assembly (SMCI) → Data center build-out → NeoCloud operations (GPU-as-a-service) → Enterprise/AI lab customers → Model training and inference delivery

**THE BOTTLENECK:** Power + GPU allocation (now shifting to power as primary):
- Microsoft CEO: "We have GPUs in inventory we can't plug in" — power is now the binding constraint
- GPU-as-a-service market: $3.23B (2023) → $49.84B (2032); 36% CAGR
- CoreWeave: ~$1 billion quarterly revenue; $5B projected annual revenue; NVIDIA backs CoreWeave with equity and GPU allocation priority — creates a moat vs. bootstrapped entrants
- CoreWeave pricing: $1.39/hour A100 vs. $3.67/hour on Azure (62% cost advantage)
- Infrastructure supply = main bottleneck to reach projected 200 GW of training/inference compute by 2030

**Bottleneck Owners (listed):**
- **CRWV (CoreWeave)** — Listed February 2026; leading NeoCloud; NVIDIA-backed; $5B annual revenue; $55B backlog (including $14B Meta deal); Pricing power = HIGH (GPU availability + existing power contracts are hard to replicate).
- **SMCI (Super Micro Computer)** — AI server OEM; first-mover in liquid cooling rack systems; ships complete GPU racks to NeoCloud operators; primary NVIDIA server design partner. Pricing power = MODERATE-HIGH (design lead, but competition from Dell).
- **NTAP (NetApp)** — Storage layer for AI clusters (all-flash arrays for training data); AI data pipeline management. Pricing power = MODERATE.
- **CORZ (Core Scientific)** — Former Bitcoin miner pivoting to AI HPC hosting; owns power contracts and real estate; ~$500M in AI HPC contracts signed. Pricing power = MODERATE (differentiated by existing power).

**Demand Drivers:**
1. Hyperscaler GPU scarcity overflow: AWS/Azure/GCP capacity constraints push enterprises to NeoCloud alternatives
2. Frontier AI model training: each new generation requires 10–100x more compute; CoreWeave positioned as NVIDIA's preferred deployment partner
3. AI inference scale: inference workloads growing faster than training; NeoCloud enables burst capacity without capital lock-in

**Adoption Phase (2026):** GROWTH — NeoCloud boom cycle, capital formation accelerating

**NRGC Phase signal:** CRWV Phase 2–3 (just listed; institutional discovery beginning). SMCI Phase 3 (known AI infrastructure play; valuation reset risk after accounting issues in 2024–2025). CORZ Phase 2 (pivot story; narrative building).

**Bear Case:**
- Power costs make NeoCloud economics unviable vs. hyperscaler vertical integration
- NVIDIA cuts GPU allocation to NeoCloud in favor of direct hyperscaler deals
- SMCI: audit/accounting overhang from 2024 delays (SEC investigation; new auditor risk)
- NeoCloud commoditization (price per GPU-hour races to zero as supply normalizes)
- CORZ: Bitcoin mining heritage creates volatility and narrative confusion

**Key Monitoring Signals:**
- CRWV quarterly revenue, backlog, and power MW contracted
- GPU spot market pricing (H100/B200 rental rates) — divergence from contract = demand signal
- SMCI shipment volumes and liquid cooling attach rate
- CoreWeave customer concentration (Meta/Microsoft = >50% risk)
- CORZ HPC hosting contracted MW and revenue ramp vs. BTC mining revenue decline

---

## Theme 10: AI Infrastructure (VRT, DELL, ANET, APH)

**Value Chain:**
Network switching fabric (ANET) → Server/rack systems (DELL, SMCI) → Power + cooling (VRT) → Interconnect/cabling (APH) → Rack integration → Data center deployment → Hyperscaler operation

**THE BOTTLENECK:** Three concurrent constraints:
1. **High-speed Ethernet switching** (ANET): 800G switch ASIC supply; moving to 1.6T; ANET controls enterprise AI networking with 33%+ share
2. **Liquid cooling supply chain** (VRT): specialized components (heat exchangers, CDUs) in multi-year lead times; VRT backlog at $15B
3. **High-density interconnects** (APH): Amphenol controls ~33% of AI data center interconnect market; high-speed copper and fiber connectors critical for every rack

**Bottleneck Owners (listed):**
- **VRT (Vertiv)** — See Theme 7. Q4 2025 orders +252%; liquid cooling at 40% CAGR through 2028. Pricing power = VERY HIGH.
- **DELL (Dell Technologies)** — AI server OEM + integrated solutions (PowerEdge XE for AI); partnered with VRT for prefab AI-ready data centers; revenue from AI server sales accelerating. Pricing power = MODERATE (competitive with SMCI, HPE).
- **ANET (Arista Networks)** — Dominant AI data center Ethernet switching; $9B FY2025 revenue (+29% YoY); 2026 AI networking revenue target raised to $3.25B; beat Cisco in data center switching market share. Pricing power = HIGH (EOS software + customer stickiness).
- **APH (Amphenol)** — High-density interconnects and fiber connectors; 33% AI data center interconnect share; secular beneficiary of every rack deployed. Pricing power = HIGH.

**Demand Drivers:**
1. GPU cluster expansion: every 100,000-GPU cluster requires thousands of switches, millions of connectors, and MW-scale liquid cooling systems
2. Liquid cooling mandate: NVIDIA's GB200 NVL72 requires liquid cooling (not air); VRT's 7MW reference architecture is the standard
3. Network disaggregation: enterprises adopting open networking → ANET's EOS operating system + CloudVision management

**Adoption Phase (2026):** GROWTH — all components in active ramp, multi-year backlog visibility

**NRGC Phase signal:** ANET Phase 3–4 (Institutional → Narrative Expansion). VRT Phase 3 (order surge confirming institutional discovery). APH Phase 3 (steady compounder accelerating). DELL Phase 2–3 (AI server pivot gaining recognition).

**Bear Case:**
- Custom silicon (e.g., Broadcom Tomahawk 5) commoditizes switching → margin pressure on ANET
- VRT execution risk on liquid cooling ramp (manufacturing complexity)
- Trade tariffs on Chinese components (VRT copper/steel exposure; 145% tariff impact in 2025)
- APH: breadth of products makes AI-specific growth harder to isolate; macro cyclicality

**Key Monitoring Signals:**
- ANET Q/Q revenue and AI-specific revenue guidance
- VRT orders/backlog growth rate (deceleration = early warning)
- APH connector revenue growth and book-to-bill ratio
- DELL AI server order intake (PowerEdge AI product line)
- 800G→1.6T transceiver volume (interconnect demand proxy)

---

## Theme 11: Data Center Infrastructure / Power (PWR, EME, AMPS, GLDD)

**Value Chain:**
Land permitting → Grid interconnect negotiations (18–36 months) → Substation/transmission build (utility side) → On-site electrical infrastructure (switchgear, transformers, generators) → Civil/structural construction → Commissioning → Ongoing maintenance/upgrade

**THE BOTTLENECK:** Electrical contractor capacity + transformer supply:
- Grid interconnection queue: multi-year backlog at FERC; most projects waiting 2–4 years for utility interconnect approval
- Transformer shortage: >50% of US data centers delayed; large power transformer lead times 3–5 years; US transformer market dominated by GE Vernova, Eaton, Siemens ($1.49B market, moderately consolidated)
- Skilled electrical workforce: shortage of licensed high-voltage electricians, substation engineers; demand outpacing trade school output by 3:1

**Bottleneck Owners (listed):**
- **PWR (Quanta Services)** — Largest US specialty electrical contractor; Q1 2026 adjusted EPS $2.68 vs. $2.03 expected; revenue $7.87B (+26.33%); record backlog **$48.5B**. Sole-source for major transmission and data center electrical work. Pricing power = VERY HIGH (irreplaceable skilled workforce + relationship moat).
- **EME (EMCOR Group)** — Second-largest electrical/mechanical contractor; data center specialist; strong position in hyperscaler construction. Pricing power = HIGH.
- **AMPS (Altus Power)** — Distributed solar + storage for data centers; C&I clean energy provider; relevant for on-site power solutions bypassing grid queues. Pricing power = MODERATE.
- **GLDD (Great Lakes Dredge & Dock)** — Offshore wind + coastal civil works; less direct data center play; relevant for offshore transmission cable projects. Pricing power = MODERATE.

**Demand Drivers:**
1. Data center construction boom: $650–720B hyperscaler capex → proportional electrical and civil infrastructure spend
2. Grid modernization: Biden/Trump bipartisan support for transmission investment; $73B allocated in IRA for grid; aging infrastructure requires replacement
3. Clean energy interconnection: utility-scale solar/wind farms require transmission upgrades; PWR is primary beneficiary

**Adoption Phase (2026):** GROWTH — multi-year backlog visibility; this is the picks-and-shovels play within picks-and-shovels

**NRGC Phase signal:** PWR Phase 3 (Institutional Discovery confirmed; record backlog + EPS beat = institutional validation). EME Phase 2–3 (less recognized). AMPS Phase 2 (early acceleration in C&I solar).

**Bear Case:**
- Data center construction freezes (capex pullback) → backlog drawdown
- Labor cost inflation erodes contractor margins
- GLDD: offshore wind policy risk under current administration
- AMPS: IRA clean energy credit rollback reduces solar economics

**Key Monitoring Signals:**
- PWR backlog level quarterly (absolute level and YoY growth — $48.5B is current record)
- EME data center-specific revenue segment
- Grid interconnection queue length (FERC — monthly) = leading indicator of 2–3 year future demand
- US transmission line construction starts (Edison Electric Institute annual)
- Transformer manufacturing capacity additions (GE Vernova, Eaton earnings commentary)

---

## Theme 12: Drone / UAV (ACHR, JOBY, RCAT, AVAV)

**Value Chain:**
Propulsion system (electric motors, engines) → Airframe / composite manufacturing → Avionics + autonomy stack → Flight control software → Ground control stations → Regulatory certification (FAA Part 135/141/Type Cert) → Commercial/military deployment → Data/service layer

**THE BOTTLENECK:** Regulatory certification is the binding constraint:
- No eVTOL has achieved FAA Type Certification as of May 2026
- Joby: Stage 4 of 5 FAA certification stages; TIA aircraft completed; FAA pilot flights scheduled 2026. Target: commercial launch 2026 (likely 2027 slip)
- Archer: MOC accepted January 2026; only 15% complete on Type Certification as of August 2025
- Secondary bottleneck: battery energy density (300–400 Wh/kg needed at scale; current ~250 Wh/kg for aviation-grade)
- Military drone bottleneck: skilled operators and analysts; BVLOS (Beyond Visual Line of Sight) regulatory framework still evolving

**Market sizes:**
- Global drone market: $83.8B (2025) → $182.4B (2033); CAGR 9.5%
- Military UAV: $15.8B (2025) → $22.8B (2030); CAGR 7.6%
- eVTOL/VTOL UAV: $7.1B (2025) → $25.6B (2032); CAGR 20.1%

**Bottleneck Owners (listed):**
- **JOBY Aviation** — Most advanced eVTOL certification; Stage 4/5 FAA; U.S. Air Force deliveries begun; Toyota partnership (manufacturing scale); target 4 aircraft/month by 2027, 500/year at Dayton facility. Pricing power = MODERATE (first-mover; certification moat temporary).
- **ACHR (Archer Aviation)** — Midnight eVTOL; Anduril defense partnership (military UAV adaptation); ARC Georgia facility ramping; MOC accepted Jan 2026. Behind JOBY but second-mover advantage in defense. Pricing power = MODERATE.
- **RCAT (Red Cat Holdings)** — Military small drone manufacturer; Pentagon's Red UAS-approved vendor; EDGE 130 Blue quadcopter for battlefield use. Niche military bottleneck (only US-made alternatives at scale). Pricing power = HIGH in Pentagon-approved segment.
- **AVAV (AeroVironment)** — Established military UAV leader (Raven, Puma, Switchblade loitering munition); government revenue $600M+; proven battlefield system. Pricing power = HIGH (multi-decade DoD relationship + ITAR moat).

**Demand Drivers:**
1. DoD autonomous systems procurement: Pentagon target 200,000 autonomous systems; $9.8B FY2026 budget for unmanned; Ukraine war validation of drone warfare
2. Urban air mobility: Dubai, Singapore, LA (2028 Olympics) as initial markets; Joby targeting ride-hailing launch
3. Commercial delivery: Amazon Prime Air, Wing (Alphabet) scaling; BVLOS framework enabling last-mile logistics

**Adoption Phase (2026):** EARLY for eVTOL (certification phase); GROWTH for military UAV (active procurement)

**NRGC Phase signal:** AVAV Phase 3 (steady grower, proven). RCAT Phase 2 (early acceleration in military niche). JOBY/ACHR Phase 1–2 (pre-revenue commercial; high narrative vs. reality ratio — be cautious on sizing).

**Bear Case:**
- FAA Type Certification delays push eVTOL commercial launch to 2028–2029
- Battery energy density improvements stall (range limitation kills urban economics)
- Chinese eVTOL manufacturers (EHang, Autoflight) gain regulatory approval in international markets
- Military drone budget cut in continuing resolution
- Joby/Archer dilutive capital raises (both burning $300M+/year)

**Key Monitoring Signals:**
- FAA Type Certification progress announcements (stage completion = binary catalyst)
- JOBY aircraft production count and flight test hours
- RCAT Pentagon contract pipeline (Red UAS approved vendor list updates)
- AVAV revenue and Switchblade munition delivery volumes
- Battery energy density benchmarks from major suppliers (CATL, Panasonic aviation-grade)

---

## Theme 13: Robotics (ISRG, TER, BRKS, TSLA Optimus)

**Value Chain:**
Actuator manufacturing (motors, harmonic drives, pneumatics) → Sensor systems (vision, force/torque, LiDAR) → Robot compute (edge AI chips) → Software/AI stack (manipulation, navigation) → End-effector / tooling → System integration → Deployment → Maintenance/uptime

**THE BOTTLENECK:** Dexterous manipulation AI + actuator supply:
- Harmonic drives (strain wave gearboxes) and high-precision actuators: concentrated in Japan (Harmonic Drive Systems; Nabtesco); no US equivalent at scale; 12–18 month lead times
- AI software for dexterous manipulation: foundation model for robot learning still pre-production; Tesla Optimus manufacturing cost $50K–100K vs. $20K target; production miss (hundreds vs. 5,000 target in 2025)
- Global humanoid installations: 16,000 units total through 2025; Chinese companies (AgiBot 31% share, Unitree ~5,500 units) dominate; Tesla has ~5% share

**Market size:** Humanoid market $4–5B (2026); expected to reach >$50B by 2030 if scaling milestones hit.

**Bottleneck Owners (listed):**
- **ISRG (Intuitive Surgical)** — Surgical robotics monopoly (da Vinci); $8B+ annual revenue; 88% market share in robotic surgery; installed base of 9,000+ systems globally. Pricing power = EXTREME (10-year+ equipment lock-in; consumable revenue stream). Adjacent to general robotics but the most proven business model.
- **TER (Teradyne)** — Universal Robots (collaborative robots / cobots) + semiconductor test equipment; cobot pioneer with 60%+ market share in cobots; transitioning to AI-enabled cobots. Pricing power = HIGH.
- **BRKS (Brooks Automation → Azenta)** — Semiconductor automation + life sciences sample management; high-precision automation for fab and lab environments. Pricing power = HIGH in semiconductor fab automation.
- **TSLA (Tesla Optimus)** — Humanoid robot; internal factory deployment first (Fremont/Shanghai); external sales targeted 2026–2027; manufacturing cost barrier is THE bottleneck. Pricing power = SPECULATIVE (unproven at scale).

**Demand Drivers:**
1. Labor cost arbitrage: humanoid robots at $20K/unit (target) vs. $60–80K/year for factory workers; automation ROI positive if unit costs hit targets
2. Semiconductor fab automation: chip fabs require ultra-precise automation; zero-defect requirement → BRKS and ISRG adjacencies
3. Surgical robotics expansion: robotic surgery market growing 15%+ annually; ISRG moving to soft-tissue and single-port systems (SP system)

**Adoption Phase (2026):** ISRG: GROWTH-LATE (dominant franchise). TER: GROWTH. BRKS: GROWTH. TSLA Optimus: EARLY (demonstration phase, not commercial).

**NRGC Phase signal:** ISRG Phase 4 (Narrative Expansion, mature compounder). TER Phase 2–3. BRKS Phase 2. TSLA Optimus Phase 1–2 (real product but narrative >> delivery; high speculation premium).

**Bear Case:**
- Chinese humanoid robots (AgiBot, Unitree) win on cost → international market lost to US players
- Harmonic drive supply doesn't scale fast enough → limits all humanoid producers
- ISRG: antitrust scrutiny on monopoly pricing of consumables
- TER: cobot market commoditization as new entrants (Fanuc, Yaskawa) copy designs
- TSLA Optimus: CEO distraction risk; manufacturing cost target may never be achieved

**Key Monitoring Signals:**
- ISRG da Vinci system placements quarterly + procedure volume growth
- TSLA Optimus production numbers (quarterly earnings disclosure)
- Harmonic Drive Systems (Japan-listed) order book and delivery lead times
- TER Universal Robots revenue and cobot unit shipments
- Chinese humanoid unit shipments (AgiBot, Unitree factory announcements)

---

## Theme 14: Connectivity / Satellite (TMUS, ASTS, ERIC, NOK)

**Value Chain:**
Spectrum licensing → RAN (Radio Access Network) infrastructure → Core network + software-defined networking → Backhaul (fiber/satellite) → End-device (handset/IoT) → Service layer → Direct-to-device (satellite layer above all)

**THE BOTTLENECK:** Two distinct sub-themes with different bottlenecks:

**Terrestrial 5G (TMUS):**
- Spectrum is the primary bottleneck: 2.5 GHz mid-band is the sweet spot (range + speed balance); TMUS has 3x more 2.5 GHz mid-band spectrum than AT&T and Verizon combined — this is a structural, multi-decade moat
- TMUS: 305M people covered by Ultra Capacity 5G; 8.5M FWA (fixed wireless access) subscribers (+31% YoY); 13 consecutive quarters as broadband industry customer growth leader

**Satellite Direct-to-Device (ASTS):**
- The bottleneck is satellite constellation scale and launch cadence
- ASTS BlueBird: 6 satellites launched; targeting 45–60 by end 2026; $1B committed revenue from 50+ MNOs (AT&T, Verizon, Vodafone); each satellite covers millions of km²
- Nationwide intermittent coverage achievable with 25 sats; continuous coverage requires 90–168 sats (2028 timeline)
- AST5000 ASIC: 10 GHz processing bandwidth per sat; 120 Mbps per cell; largest commercial phased arrays ever (2,400 sq ft)

**Bottleneck Owners (listed):**
- **TMUS (T-Mobile US)** — Spectrum moat: 2.5 GHz mid-band holdings dwarf rivals; 5G standalone architecture (AI-native); cloud-native core (only US carrier with full SA 5G); 2030 targets: 18–19M broadband customers. Pricing power = HIGH (network quality gap vs. AT&T/Verizon is measurable and widening).
- **ASTS (AST SpaceMobile)** — Direct-to-device satellite broadband; 50+ MNO partner network with 3B subscribers; $1B committed revenue; technology moat (massive phased array + AST5000). Pre-revenue but revenue commencing 2026. Pricing power = VERY HIGH if network builds out (only direct-to-standard-handset system at scale; Starlink requires special terminals).
- **ERIC (Ericsson)** — Network equipment vendor; 5G RAN and core; structural beneficiary of global 5G build-out and O-RAN transition; AI-native network management. Pricing power = HIGH in RAN (duopoly with Nokia).
- **NOK (Nokia)** — 5G RAN + cloud core; reStructuring complete; Bell Labs IP portfolio; O-RAN open architecture advocate. Pricing power = MODERATE-HIGH (behind Ericsson in mobile share).

**Demand Drivers:**
1. Fixed Wireless Access (FWA): TMUS FWA approaching 10M subs; 5G home internet replacing cable in underserved markets; TAM = 50M US households without fiber
2. Direct-to-device global connectivity: 3B+ unserved mobile subscribers globally; ASTS's model bypasses terrestrial infrastructure entirely
3. Private 5G networks: industrial automation, defense, port/logistics operators building dedicated 5G networks → ERIC/NOK enterprise opportunity

**Adoption Phase (2026):** TMUS: GROWTH-LATE (dominant incumbent compounder). ASTS: EARLY-GROWTH (first revenue 2026, scaling 2027–2028). ERIC/NOK: GROWTH (5G global rollout mid-cycle).

**NRGC Phase signal:** TMUS Phase 3–4 (Institutional → compounder). ASTS Phase 2 (Early Acceleration — first commercial revenue 2026 = the inflection). ERIC/NOK Phase 3 (steady institutional holdings).

**Bear Case:**
- ASTS satellite failures or launch delays (already slipped from original schedule)
- SpaceX Starlink adds direct-to-cell capability at scale (already testing; Starlink has 7,000+ satellites)
- TMUS spectrum advantage erodes if AT&T/Verizon acquire DISH spectrum portfolio
- ERIC/NOK: Huawei re-entry into Western markets (currently banned in US/EU but geopolitical reversal risk)
- US spectrum auction delays (FCC authority lapse risk)

**Key Monitoring Signals:**
- ASTS satellite launch schedule and constellation count (monthly)
- ASTS commercial service launch announcement + MNO partner activation
- TMUS FWA net adds quarterly (must hold 400K+/quarter)
- TMUS mid-band spectrum utilization and average download speed vs. peers (Ookla/Opensignal monthly)
- ERIC/NOK 5G RAN orders from Tier-1 MNOs (quarterly backlog)
- Starlink direct-to-cell commercial availability and pricing (existential risk to ASTS narrative)

---

## Summary Bottleneck Map (Quick Reference)

| Theme | Primary Bottleneck | Bottleneck Owner | NRGC Phase |
|-------|-------------------|-----------------|-----------|
| 1. AI-Related | CoWoS packaging + grid power | NVDA (GPU) / VRT+ETN (power) | 3–4 |
| 2. Memory/HBM | HBM wafer capacity + yield | SK Hynix / MU | 3 |
| 3. Space | Launch capacity (medium-lift gap) | RKLB / SpaceX (private) | 2–3 |
| 4. Quantum | Error correction technology | IONQ / IBM | 1–2 |
| 5. Photonics | EML laser chip (InP wafer) | LITE / COHR | 2–3 |
| 6. DefenseTech | Cleared developer talent + platform data moat | PLTR / AXON | 3–4 |
| 7. Data Center | Power / transformer / grid interconnect | VRT / ETN / EQIX | 3–4 |
| 8. Nuclear/SMR | NRC licensing timeline + HALEU enrichment | CEG / CCJ (near-term) | 1–2 (SMR) / 3 (CEG) |
| 9. NeoCloud | Power contracts + GPU allocation | CRWV / CORZ | 2–3 |
| 10. AI Infrastructure | Liquid cooling + switching + interconnects | VRT / ANET / APH | 3–4 |
| 11. DC Infra/Power | Grid interconnect + transformer manufacturing | PWR / EME | 3 |
| 12. Drone/UAV | FAA Type Certification | AVAV (military) / JOBY (eVTOL) | 1–2 (eVTOL) / 3 (military) |
| 13. Robotics | Actuator supply + manipulation AI | ISRG (proven) / TER | 2–4 (varies) |
| 14. Connectivity | Spectrum (TMUS) + satellite constellation scale (ASTS) | TMUS / ASTS | 2–4 (varies) |

---

*Data verified to May 2026. Sources: IEA Energy and AI report, TrendForce HBM market reports, Goldman Sachs AI capex analysis, SpaceNews launch economics, Riverlane quantum error correction, McKinsey photonics supply analysis, MarketsandMarkets defense AI, S&P Global data center power demand, Nareit data center REIT analysis, FAA eVTOL certification tracker.*
