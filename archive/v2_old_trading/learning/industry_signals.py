"""
AlphaAbsolute — Industry-Specific NRGC Signal Library
Per-industry definitions: what does each NRGC phase LOOK LIKE for this sector?

This is the "domain expertise" layer. Different industries have different signals
for the same NRGC phase. Memory cycles !== AI cycles !== Space cycles.

Used by: nrgc_tracker.py, distill_engine.py
Token cost: $0 (pure Python data)
"""

# ─── Per-Industry NRGC Signal Definitions ─────────────────────────────────────
# Each industry defines:
#   phase_X_signals: list of specific observable signals that indicate phase X
#   key_metrics: the numbers to watch (tracked per earnings release)
#   lead_indicators: which OTHER companies' results give early signals
#   narrative_templates: the typical narratives for each phase
#   phase_duration_weeks: typical duration of each phase (calibration guide)

INDUSTRY_NRGC = {

    # ── Theme 2: Memory / HBM ──────────────────────────────────────────────────
    "Memory/HBM": {
        "tickers": ["MU", "WDC", "MRAM"],
        "lead_indicators": ["Samsung earnings", "SK Hynix earnings", "TSMC guidance", "NVIDIA HBM demand"],
        "key_metrics": [
            "DRAM ASP QoQ change (%)",
            "NAND ASP QoQ change (%)",
            "HBM revenue share (%)",
            "Gross margin (%)",
            "Inventory days",
            "Utilization rate (%)",
            "Bit shipment growth QoQ",
        ],
        "phase_duration_weeks": {1: 26, 2: 16, 3: 12, 4: 20, 5: 10, 6: 8, 7: 16},
        "phase_signals": {
            1: [  # Neglect — inventory glut, pricing collapse
                "DRAM/NAND ASP declining double-digit QoQ",
                "Inventory days above 120+",
                "Utilization rate cut to 70% or below",
                "Management language: 'challenging environment', 'reducing utilization'",
                "Multiple analyst downgrades",
                "Capex cuts announced",
                "YoY revenue negative 30%+",
            ],
            2: [  # Accumulation — bottom forming
                "ASP decline decelerating (still negative but less negative)",
                "Inventory days starting to shrink from peak",
                "Utilization stabilizing (not cutting further)",
                "Management language: 'seeing early stabilization', 'demand improving in pockets'",
                "Beat low expectations but still guide cautiously",
                "First analyst saying 'worst is behind us'",
                "Insider buying quietly starting",
                "HBM allocation mentioned for first time",
            ],
            3: [  # Inflection — the setup ⭐
                "DRAM ASP positive QoQ for first time in cycle",
                "HBM revenue becoming material (10%+ of DRAM revenue)",
                "Utilization rate rising above 85%",
                "Gross margin recovering — still below peak but clearly inflecting",
                "Management language: 'supply tighter than expected', 'strong pricing environment'",
                "Revenue guidance RAISED for first time",
                "AI hyperscaler confirmed as major HBM customer",
                "Lead times extending for HBM",
                "First 'buy' initiations from previously bearish analysts",
                "Stock making new 52-week highs on volume",
            ],
            4: [  # Recognition — narrative fully formed
                "DRAM ASP accelerating (2nd/3rd QoQ increase)",
                "HBM 30%+ of DRAM revenue",
                "Gross margin approaching prior peak levels",
                "Management language: 'supply constrained', 'pricing power', 'record backlog'",
                "Consecutive beat-and-raise quarters",
                "Multiple analyst upgrades with raised price targets",
                "Institutional 13F shows new large positions",
                "EPS estimates revised significantly higher",
            ],
            5: [  # Consensus — priced in
                "Revenue still growing but QoQ growth rate decelerating",
                "Gross margin at or near peak, hard to expand further",
                "Management language: still positive but more measured",
                "ASP growth slowing (every buyer already has inventory)",
                "All analysts bullish — no bears left (crowded)",
                "ETF flows heavily into sector",
                "New competitor capacity announcements increasing",
            ],
            6: [  # Euphoria — danger zone
                "Customer inventory building mentioned for first time",
                "Hyperscaler capex showing signs of slowing",
                "AI training workloads shifting from training to inference (less HBM intensive)",
                "Management language: 'digestion period ahead', 'normal seasonality'",
                "ASP growth zero or turning negative",
                "Multiple IPOs in memory-adjacent space",
                "Price-to-book hitting historical extremes",
            ],
            7: [  # Distribution — exit
                "Guidance CUT — revenue lower than expected",
                "Gross margin declining QoQ",
                "Customer inventory overbuild explicitly mentioned",
                "Utilization cut announcements",
                "Good earnings but stock sells off — distribution",
                "Large institutional 13F shows position cuts",
                "Memory industry conference tone turns cautious",
            ],
        },
        "narrative_templates": {
            1: "Memory market in downcycle. Excess inventory from {prior_cycle_peak} demand. ASP declining, utilization cutting. Avoid.",
            2: "Memory downcycle bottom forming. HBM emerging as new demand vector for AI. Accumulation zone for patient investors.",
            3: "Memory supercycle inflection. HBM demand from AI infrastructure (NVIDIA H100/H200/B200) exceeding supply. ASP recovering. Entry zone.",
            4: "Memory supercycle in full swing. HBM supply constrained vs AI demand. Beat-and-raise pattern. Hold full position.",
            5: "Memory cycle peak approaching. Strong fundamentals but fully priced. Hold but watch for deceleration signals.",
            6: "Memory cycle late stage. Euphoric pricing. Trim aggressively — next downcycle forming.",
            7: "Memory downcycle beginning. Customer inventory buildup. Exit.",
        },
        "best_entry_phase": [2, 3],
        "exit_warning_phase": [5, 6],
    },

    # ── Theme 1: AI Infrastructure / GPU ──────────────────────────────────────
    "AI Infrastructure": {
        "tickers": ["NVDA", "AVGO", "ANET", "MRVL"],
        "lead_indicators": [
            "Hyperscaler capex guidance (MSFT, META, GOOGL, AMZN)",
            "NVIDIA data center revenue",
            "TSMC AI accelerator revenue",
            "Power infrastructure orders (VRT, ETN, EME)",
        ],
        "key_metrics": [
            "Data center revenue QoQ (%)",
            "Hyperscaler capex guidance (aggregated)",
            "GPU backlog / lead times (weeks)",
            "Networking revenue (400G/800G transition)",
            "Power density demand (kW per rack)",
            "AI cluster size growth",
        ],
        "phase_duration_weeks": {1: 12, 2: 8, 3: 16, 4: 24, 5: 12, 6: 8, 7: 12},
        "phase_signals": {
            1: [
                "Hyperscaler capex growth pausing or declining",
                "GPU inventory at hyperscalers building",
                "ROI on AI investment questioned by analysts/boards",
                "ChatGPT-type demand normalizing from initial surge",
                "Management language: 'digesting prior investments'",
            ],
            2: [
                "Hyperscaler capex stable to slight increase",
                "New AI model (GPT-5, Gemini Ultra, etc.) driving new compute demand",
                "Management language: 'early innings', 'investment cycle beginning'",
                "GPU allocation tightening again after period of easing",
            ],
            3: [
                "Hyperscaler capex RAISED guidance — multiple companies same quarter",
                "NVIDIA data center revenue beating estimates significantly",
                "GPU lead times extending to 30+ weeks",
                "Networking (400G/800G InfiniBand/Ethernet) orders surging",
                "Power infrastructure orders spiking",
                "New AI model requires 10x+ more compute than prior generation",
                "Sovereign AI / national AI programs announced",
                "Management language: 'supply constrained', 'every customer wants more'",
            ],
            4: [
                "Sequential capex increases every quarter from all hyperscalers",
                "New GPU architecture released ahead of schedule",
                "Enterprise AI deployment moving from pilot to production",
                "Software monetization of AI visible (copilot revenue etc.)",
                "Supply chain (CoWoS packaging, HBM) being built out to meet demand",
            ],
            5: [
                "Hyperscaler capex growth rate decelerating from peak",
                "AI ROI debate more prominent — 'when does this pay off?'",
                "GPU alternatives (custom silicon ASIC) gaining market share",
                "Management language: 'more balanced' supply/demand",
                "Crowded trade — everyone has NVDA in portfolio",
            ],
            6: [
                "Hyperscaler capex guidance FLAT or cut",
                "Custom ASIC (Google TPU, Amazon Trainium) taking significant share",
                "AI inference cheaper than training — less HBM/compute intensive",
                "China export restrictions biting into revenue",
                "Multiple AI startup failures — 'AI winter' narrative emerging",
            ],
            7: [
                "Multiple hyperscaler capex cuts in same quarter",
                "NVIDIA guidance miss for first time",
                "AI ROI failure high-profile (bankruptcy, writedowns)",
                "Enterprise AI adoption stalling — not meeting productivity promises",
            ],
        },
        "narrative_templates": {
            2: "AI capex cycle bottom. New model releases forcing compute upgrade. Early accumulation.",
            3: "AI infrastructure supercycle. Hyperscalers committed to $200B+ annual capex. NVIDIA supply constrained. Maximum conviction entry.",
            4: "AI buildout in execution phase. Enterprise adoption beginning. Beat-and-raise continues.",
            5: "AI infrastructure mature cycle. Strong but crowded. ROI debate beginning.",
            6: "AI cycle late stage. Custom silicon threat real. Trim aggressively.",
        },
        "best_entry_phase": [2, 3],
        "exit_warning_phase": [5, 6],
    },

    # ── Theme 3: Space ─────────────────────────────────────────────────────────
    "Space": {
        "tickers": ["RKLB", "LUNR", "AST", "ASTS", "RDW"],
        "lead_indicators": [
            "US government defense budget (space line items)",
            "NASA budget and contracts",
            "SpaceX launch cadence (validates market)",
            "Satellite internet subscribers (AST proxy)",
        ],
        "key_metrics": [
            "Launch cadence (launches per quarter)",
            "Contract backlog ($)",
            "Revenue per launch ($M)",
            "Satellite constellation progress",
            "Government vs commercial revenue mix",
            "Gross margin trajectory",
        ],
        "phase_duration_weeks": {1: 20, 2: 16, 3: 20, 4: 30, 5: 16, 6: 12, 7: 20},
        "phase_signals": {
            1: [
                "Failed launches destroying investor confidence",
                "Government budget uncertainty",
                "SPAC-era space company going bankrupt (Virgin Orbit, Astra)",
                "Management language: 'rightsizing', 'extending runway'",
                "No commercial customers announced",
            ],
            2: [
                "Launch success rate improving",
                "First government contract won (anchor customer)",
                "Management language: 'backlog growing', 'commercial demand pipeline'",
                "SpaceX dominant but ecosystem players getting work",
                "Orbital debris/satellite insurance market forming",
            ],
            3: [
                "Reusable rocket achieving cost targets",
                "Commercial satellite constellation starting deployment",
                "Government classified contract wins (large backlog)",
                "First paying customers on new constellation service",
                "Revenue guidance raised based on backlog conversion",
                "Gross margin turning positive for first time",
                "Strategic partnership with major defense contractor",
            ],
            4: [
                "Launch cadence accelerating each quarter",
                "Satellite internet subscribers growing rapidly",
                "Multiple government customers (not just NASA/DoD)",
                "International expansion — allied nation contracts",
                "Revenue growing 50%+ YoY consistently",
            ],
            5: [
                "Competition increasing — Blue Origin, other new entrants",
                "Government budget debates creating uncertainty",
                "Valuation stretched — market cap > realistic near-term revenue",
                "Management vision expanding faster than execution",
            ],
            6: [
                "Launch failures — even one damages credibility significantly",
                "Government contract delays or cancellations",
                "Satellite constellation economics questioned",
                "Cash burn at unsustainable rate",
            ],
            7: [
                "Dilutive equity raise at poor terms",
                "Major government contract lost",
                "Constellation milestone missed",
            ],
        },
        "narrative_templates": {
            2: "Space economy early innings. Government anchor contracts forming. Speculative position only.",
            3: "Space commercialization inflection. Reusability economics proven. Government + commercial backlog building. Entry zone.",
            4: "Space economy scaling. Constellation deployment, cadence growing. Full position.",
            5: "Space sector mature. Competition intensifying. Hold selectively.",
        },
        "best_entry_phase": [2, 3],
        "exit_warning_phase": [5, 6],
    },

    # ── Theme 8: Nuclear / SMR ─────────────────────────────────────────────────
    "Nuclear/SMR": {
        "tickers": ["NNE", "OKLO", "CEG", "CCJ", "UEC"],
        "lead_indicators": [
            "US utility power purchase agreements",
            "Data center power demand growth",
            "DOE loan guarantee program approvals",
            "NRC regulatory approval timeline",
            "Uranium spot price",
            "EU taxonomy inclusion for nuclear",
        ],
        "key_metrics": [
            "PPA (Power Purchase Agreements) signed (GW)",
            "NRC license application progress",
            "Uranium supply contract coverage",
            "Government grant/loan guarantee ($B)",
            "Data center customer commitments",
            "First criticality milestone date",
        ],
        "phase_duration_weeks": {1: 52, 2: 30, 3: 24, 4: 48, 5: 20, 6: 16, 7: 30},
        "phase_signals": {
            1: [
                "Fukushima/Chernobyl hangover — political opposition dominant",
                "No new reactor construction permits",
                "Existing plants unprofitable, closing",
                "Nuclear narrative = 'too expensive, too slow, too dangerous'",
                "Uranium price at decade lows",
            ],
            2: [
                "Germany/Japan reconsidering closures",
                "DOE providing first funding for advanced reactor concepts",
                "Tech company mentions carbon-free baseload power need",
                "First SMR design submitted to NRC for review",
                "Uranium price starting to recover",
                "Management language: 'regulatory clarity improving'",
            ],
            3: [
                "Microsoft/Google/Amazon sign nuclear PPA (data center demand clear)",
                "NRC approves first advanced reactor design",
                "Government loan guarantee (DOE) for SMR construction",
                "Uranium spot price above $70/lb and rising",
                "Multiple utility interest — not just tech companies",
                "Congress bipartisan support — ADVANCE Act, CHIPS-style nuclear bill",
                "Management language: 'demand exceeds our build capacity'",
                "First SMR construction contract signed",
            ],
            4: [
                "Multiple gigawatt-scale PPAs signed each quarter",
                "Construction begins on first commercial SMR",
                "Fuel supply contracts locked in",
                "International interest — Poland, UAE, Japan SMR programs",
                "Revenue beginning to show (uranium sales, engineering fees)",
            ],
            5: [
                "Every utility doing nuclear exploration — crowded",
                "SMR cost estimates rising above initial projections",
                "Regulatory delays extending timelines",
                "Competition — many SMR designs competing for same PPAs",
            ],
            6: [
                "SMR construction cost overrun — Vogtle-type situation",
                "Timeline slippage from 2028 to 2032+",
                "Hyperscaler capex pivot away from nuclear back to gas",
                "Uranium price declining — supply catching up",
            ],
            7: [
                "Major nuclear project cancellation",
                "Regulatory reversal",
                "Competing clean energy (solar + storage) achieving cost parity with SMR",
            ],
        },
        "narrative_templates": {
            2: "Nuclear renaissance early stage. AI data center power demand creating secular tailwind. Pre-revenue watchlist.",
            3: "Nuclear supercycle inflection. Tech company PPAs proving demand. DOE backing. Entry zone with 5-year horizon.",
            4: "Nuclear renaissance executing. Backlog converting to revenue. Multi-year growth.",
            5: "Nuclear mature phase. Timeline risks and cost overruns becoming real concerns. Hold selectively.",
        },
        "best_entry_phase": [2, 3],
        "exit_warning_phase": [5, 6],
    },

    # ── Theme 5: Photonics / Optical Interconnect ──────────────────────────────
    "Photonics": {
        "tickers": ["LITE", "COHR", "CIEN", "AAOI", "IIVI"],
        "lead_indicators": [
            "Hyperscaler networking capex (400G/800G/1.6T)",
            "NVIDIA InfiniBand vs Ethernet mix",
            "Data center interconnect bandwidth demand",
            "LITE/COHR quarterly earnings guidance",
        ],
        "key_metrics": [
            "Data center transceiver revenue QoQ (%)",
            "400G/800G mix (higher = better ASP)",
            "Telecom vs Data Center revenue mix",
            "Gross margin (%)",
            "Design wins at hyperscalers",
            "Lead times for 800G transceivers",
        ],
        "phase_signals": {
            1: [
                "Telecom capex cuts post 5G build",
                "Cloud capex pause — inventory overhang of 100G/400G",
                "Transceiver price erosion severe",
                "Management language: 'challenging telecom environment'",
            ],
            2: [
                "Data center starting to offset telecom declines",
                "400G adoption accelerating (replacing 100G)",
                "Management language: 'data center is bright spot'",
                "AI cluster networking requirements emerging",
            ],
            3: [
                "800G demand spike from AI cluster deployments",
                "Lead times for 800G extending 20+ weeks",
                "Hyperscaler committed large 800G/1.6T purchase orders",
                "Data center revenue exceeding telecom for first time",
                "Gross margin recovering as product mix shifts higher",
                "Design win at NVIDIA/Meta/Microsoft for AI networking",
                "Revenue guidance raised sharply",
            ],
            4: [
                "1.6T transceiver qualification beginning",
                "Multiple hyperscaler design wins converting to volume",
                "Silicon photonics (co-packaged optics) entering roadmap",
                "Gross margin at multi-year high",
            ],
            5: [
                "Competition intensifying from Taiwanese ODMs",
                "Co-packaged optics (CPO) could disrupt discrete transceiver model",
                "Revenue growth rate decelerating from peak",
                "Hyperscaler in-house optical development risk",
            ],
            6: [
                "Hyperscaler pausing 800G orders — overbought",
                "1.6T ramp slower than expected",
                "Gross margin pressure from competition",
            ],
            7: [
                "Major customer shift to in-house silicon photonics",
                "400G/800G price collapse",
                "Telecom AND data center both weak simultaneously",
            ],
        },
        "narrative_templates": {
            2: "Optical interconnect recovery. Data center offsetting telecom. Early position.",
            3: "AI interconnect supercycle. 800G demand spike from AI clusters. Entry zone.",
            4: "Photonics scaling with AI buildout. Full position.",
            5: "Optical mature — CPO risk emerging. Hold selectively.",
        },
        "best_entry_phase": [2, 3],
        "exit_warning_phase": [5, 6],
    },

    # ── Theme 6: DefenseTech ───────────────────────────────────────────────────
    "DefenseTech": {
        "tickers": ["PLTR", "CACI", "LDOS", "AXON", "BWXT"],
        "lead_indicators": [
            "US defense budget top-line",
            "Ukraine/Middle East conflict spending",
            "NATO 2% GDP commitment",
            "DoD AI/software modernization programs",
        ],
        "key_metrics": [
            "Government revenue QoQ (%)",
            "Contract backlog ($B)",
            "Book-to-bill ratio",
            "EBIT margin (%)",
            "Commercial vs Government mix",
            "AI/software revenue share",
        ],
        "phase_signals": {
            1: [
                "Budget sequestration / continuing resolution uncertainty",
                "Geopolitical stability reducing urgency",
                "Commercial tech competition (FAANG) for same contracts",
            ],
            2: [
                "Geopolitical flashpoint increasing defense urgency",
                "AI in defense becoming political priority",
                "Large contract RFP announcements",
                "Management language: 'strong pipeline', 'robust pipeline'",
            ],
            3: [
                "Major AI/software contract wins announced",
                "NATO/allied nation contracts beyond US DoD",
                "Management language: 'backlog record high', 'demand exceeds capacity'",
                "Book-to-bill > 1.2x consistently",
                "Revenue guidance raised based on backlog conversion",
            ],
            4: [
                "Congressional earmarks increasing",
                "Multi-year contracts locking in revenue visibility",
                "M&A activity in sector (consolidation)",
                "Commercial cross-sell (PLTR commercial division)",
            ],
            5: [
                "Budget cycle uncertainty (election year)",
                "Protest of major contracts causing delays",
                "Competition from traditional primes (RTX, LMT) in software",
            ],
            6: [
                "Continuing resolution blocking new contract starts",
                "Geopolitical de-escalation reducing urgency",
                "AI defense programs showing cost overruns",
            ],
        },
        "narrative_templates": {
            2: "Defense modernization early stage. AI/autonomous systems investment cycle beginning.",
            3: "DefenseTech supercycle. Software-defined warfare replacing traditional defense. Major contract wins. Entry.",
            4: "Defense AI scaling. Multi-year backlog visibility. Full position.",
        },
        "best_entry_phase": [2, 3],
        "exit_warning_phase": [5, 6],
    },

    # ── Theme 4: Quantum Computing ─────────────────────────────────────────────
    "Quantum": {
        "tickers": ["IONQ", "RGTI", "QUBT", "IBM"],
        "lead_indicators": [
            "NSF/DOE quantum funding announcements",
            "Google/IBM/Microsoft quantum milestone claims",
            "Enterprise quantum pilot programs",
        ],
        "key_metrics": [
            "Qubit count and error rate",
            "Quantum volume metric",
            "Enterprise contract bookings",
            "Government grant funding received",
            "Algorithmic qubits (logical qubits)",
        ],
        "phase_signals": {
            1: [
                "Quantum advantage claims debunked",
                "Enterprise use cases still too far out",
                "All revenue from government grants, no commercial",
                "Management language: '5-10 year horizon'",
            ],
            2: [
                "First commercial quantum-as-a-service contracts",
                "Government increasing quantum investment (CHIPS Act adjacent)",
                "Error correction milestone achieved",
                "Management language: 'fault-tolerant is closer than expected'",
            ],
            3: [
                "Enterprise pilot with Fortune 500 (pharma, finance, logistics)",
                "Quantum advantage demonstrated for specific use case",
                "Government classified contract (DOD/NSA)",
                "Hybrid quantum-classical algorithm showing commercial value",
            ],
            4: [
                "Revenue from commercial customers (not just grants)",
                "Multiple Fortune 500 deployments",
                "SaaS-like recurring quantum revenue emerging",
            ],
            5: [
                "Classical computing catching up (better algorithms)",
                "Quantum winter narrative — 'not as close as expected'",
                "Most quantum companies unprofitable with no clear path",
            ],
        },
        "narrative_templates": {
            1: "Quantum hype cycle. No commercial revenue. Avoid except tiny speculative position.",
            2: "Quantum early commercial. Government funding + first enterprise pilots. Small speculative (2-3%).",
            3: "Quantum inflection — commercial use cases proven. Increase to 5%. Still speculative.",
        },
        "best_entry_phase": [2, 3],
        "exit_warning_phase": [5],
        "max_position_pct": 5,  # speculative cap
    },

    # ── Theme 12: Drone / UAV ──────────────────────────────────────────────────
    "Drone/UAV": {
        "tickers": ["ACHR", "JOBY", "RCAT", "AVAV", "KTOS"],
        "lead_indicators": [
            "FAA eVTOL type certification progress",
            "DoD drone procurement budget",
            "Ukraine war drone attrition data",
            "Commercial drone delivery regulation",
        ],
        "key_metrics": [
            "FAA certification milestones",
            "Test flight hours",
            "DoD contract value",
            "Unit order backlog",
            "Revenue per flight (eVTOL)",
            "Cash burn rate",
        ],
        "phase_signals": {
            1: [
                "FAA certification delays",
                "eVTOL range/payload limitations vs helicopter",
                "Commercial viability questioned",
                "Only revenue from DoD small test contracts",
            ],
            2: [
                "FAA type certification submitted",
                "DoD contract for autonomous systems",
                "Ukraine war proving drone utility at scale",
                "Airlines/aircraft lessors signing LoIs",
            ],
            3: [
                "FAA type certification RECEIVED (major catalyst)",
                "First revenue commercial flight",
                "DoD large production contract",
                "Strategic partner investment (Stellantis/Toyota/airline)",
            ],
            4: [
                "Commercial route launch",
                "DoD production ramp",
                "International certification approvals",
                "Revenue guidance raised",
            ],
        },
        "narrative_templates": {
            2: "eVTOL/drone early stage. FAA certification catalyst ahead. Small speculative position.",
            3: "Drone commercialization inflection. FAA certified + DoD contract = dual revenue path.",
        },
        "best_entry_phase": [2, 3],
        "exit_warning_phase": [5, 6],
        "max_position_pct": 5,
    },

    # ── Theme 9: NeoCloud ──────────────────────────────────────────────────────
    "NeoCloud": {
        "tickers": ["CRWV", "SMCI", "NTAP", "CORZ"],
        "lead_indicators": [
            "AI startup funding rounds (GPU demand proxy)",
            "NVIDIA GPU allocation to neocloud customers",
            "Hyperscaler capacity shortage driving neocloud demand",
        ],
        "key_metrics": [
            "Revenue QoQ (%)",
            "GPU cluster size deployed",
            "Revenue per GPU-hour",
            "Customer concentration",
            "EBITDA margin",
            "GPU utilization rate",
        ],
        "phase_signals": {
            1: [
                "Traditional cloud commoditized — price war",
                "No differentiation from AWS/Azure/GCP",
                "AI workloads still small % of cloud",
            ],
            2: [
                "AI startups needing GPU capacity that hyperscalers can't provide quickly",
                "Neocloud offering spot pricing on H100s",
                "VC-backed AI startups as primary customers",
            ],
            3: [
                "Revenue surging as hyperscaler GPU allocation tightens",
                "Enterprise AI teams using neocloud for flexibility",
                "Signed multi-year GPU reservation agreements",
                "Management language: 'more demand than we can serve'",
            ],
            4: [
                "Enterprise customer mix shifting from startups",
                "Long-term contracts providing revenue visibility",
                "Geographic expansion (international)",
            ],
            5: [
                "Hyperscalers expanding GPU capacity — neocloud demand normalizes",
                "Price competition as GPU supply improves",
                "Custom silicon ASICs reducing GPU dependency",
            ],
        },
        "narrative_templates": {
            2: "NeoCloud GPU access gap. Hyperscaler waitlist driving demand. Early position.",
            3: "NeoCloud supercycle. GPU constrained hyperscalers sending enterprises to neocloud. Entry.",
        },
        "best_entry_phase": [2, 3],
        "exit_warning_phase": [5],
    },

    # ── Default fallback for unlisted themes ───────────────────────────────────
    "_default": {
        "key_metrics": ["Revenue QoQ", "Gross Margin", "Guidance", "EPS Revision"],
        "phase_signals": {
            1: ["Revenue declining", "Negative guidance", "Analyst downgrades"],
            2: ["Revenue stabilizing", "Beat low estimates", "First positive signal"],
            3: ["Revenue accelerating QoQ", "Guidance raised", "Analyst upgrades beginning"],
            4: ["Consecutive beat-and-raise", "Margin expanding", "Multiple analyst upgrades"],
            5: ["Growth rate decelerating", "Fully priced", "Crowded trade"],
            6: ["Growth stalling", "Margin peaking", "Competition intensifying"],
            7: ["Guidance cut", "Revenue miss", "Margin declining"],
        },
        "best_entry_phase": [2, 3],
        "exit_warning_phase": [5, 6],
    },
}

# ─── Phase Keywords for NLP Signal Detection ──────────────────────────────────
# Used by distill_engine to tag NRGC phase from news/earnings text

PHASE_LANGUAGE_SIGNALS = {
    "phase_2_early": [
        "early signs of stabilization", "worst is behind", "inventory normalizing",
        "demand improving in pockets", "beat expectations but guide cautiously",
        "trough", "bottom", "inflection", "first positive", "accumulation",
        "insider buying", "quietly building", "emerging demand", "early innings",
    ],
    "phase_3_inflection": [
        "supply tighter than expected", "raising guidance", "record backlog",
        "demand exceeds supply", "lead times extending", "constrained supply",
        "allocation", "sold out", "beat and raise", "first time", "new high",
        "record revenue", "unprecedented demand", "accelerating", "inflecting",
        "tipping point", "paradigm shift",
    ],
    "phase_4_recognition": [
        "consecutive quarters", "sustained momentum", "above expectations",
        "significantly raised", "secular growth", "structural demand",
        "market share gains", "pricing power", "operating leverage",
        "margin expansion", "record gross margin",
    ],
    "phase_5_consensus": [
        "fully priced", "priced to perfection", "crowded trade",
        "decelerating growth", "tough comps", "peak margins",
        "normalizing", "digest", "balanced supply demand",
    ],
    "phase_6_euphoria": [
        "parabolic", "irrational", "bubble", "frothy", "mania",
        "every investor owns", "can't go wrong", "inevitable",
        "limitless demand", "overbought",
    ],
    "phase_7_distribution": [
        "guidance cut", "miss", "disappoint", "inventory build",
        "demand destruction", "pricing pressure", "deceleration",
        "worse than expected", "below guidance", "slowing",
    ],
}

# ─── Helper Functions ──────────────────────────────────────────────────────────

def get_industry_context(theme: str) -> dict:
    """Return NRGC signal library for a given theme."""
    return INDUSTRY_NRGC.get(theme, INDUSTRY_NRGC["_default"])


def detect_phase_from_language(text: str) -> dict:
    """
    Quick pre-filter: scan text for NRGC phase language signals.
    Returns {phase_hint, signals_found, confidence}.
    """
    text_lower = text.lower()
    phase_scores = {}
    signals_found = {}

    for phase_label, keywords in PHASE_LANGUAGE_SIGNALS.items():
        hits = [kw for kw in keywords if kw.lower() in text_lower]
        if hits:
            # Map label to phase number
            phase_num = {
                "phase_2_early": 2, "phase_3_inflection": 3,
                "phase_4_recognition": 4, "phase_5_consensus": 5,
                "phase_6_euphoria": 6, "phase_7_distribution": 7,
            }.get(phase_label, 0)
            phase_scores[phase_num] = phase_scores.get(phase_num, 0) + len(hits)
            signals_found[phase_num] = hits

    if not phase_scores:
        return {"phase_hint": None, "confidence": 0, "signals": []}

    best_phase = max(phase_scores, key=phase_scores.get)
    total_hits = sum(phase_scores.values())
    confidence = min(phase_scores[best_phase] / max(total_hits, 1), 1.0)

    return {
        "phase_hint": best_phase,
        "confidence": round(confidence, 2),
        "signals": signals_found.get(best_phase, []),
        "all_scores": phase_scores,
    }


def get_phase_name(phase: int) -> str:
    names = {
        1: "Neglect", 2: "Accumulation", 3: "Inflection",
        4: "Recognition", 5: "Consensus", 6: "Euphoria", 7: "Distribution",
    }
    return names.get(phase, "Unknown")


def get_phase_action(phase: int) -> str:
    actions = {
        1: "Watchlist only",
        2: "Build 25-30% of target",
        3: "Full position — highest conviction",
        4: "Hold full position",
        5: "Hold, no add, tighten stops",
        6: "Trim to 30-40%",
        7: "Exit all",
    }
    return actions.get(phase, "Monitor")
