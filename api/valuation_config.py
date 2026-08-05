"""Configurable valuation rules for the BDC Value Map."""

VALUATION_MODEL = {
    "name": "Peer-Implied P/NAV",
    "minimumCoveragePct": 70,
    "minimumPeerCount": 3,
    "smallPeerGroupFallback": "global",
    "normalization": "peer_group_percentile_rank",
    "weights": {
        "creditPulseScore": 0.59,
        "earningsPower": 0.23,
        "navPreservation": 0.12,
        "structuralQuality": 0.06,
    },
    "componentInputs": {
        "creditPulseScore": ["currentCreditPulseScore"],
        "earningsPower": ["niiReturnOnNavPct"],
        "navPreservation": ["navPerShareChange1yPct", "navPerShareChange3yPct"],
        "structuralQuality": ["debtToEquity", "equityMarketCapitalization", "managementStructure"],
    },
    "classificationThresholds": [
        {"label": "Potentially undervalued", "min": 0.10, "className": "undervalued"},
        {"label": "Moderately undervalued", "min": 0.05, "max": 0.10, "className": "moderately-undervalued"},
        {"label": "About right", "min": -0.05, "max": 0.05, "className": "about-right"},
        {"label": "Moderately expensive", "min": -0.10, "max": -0.05, "className": "moderately-expensive"},
        {"label": "Potentially expensive", "max": -0.10, "className": "expensive"},
    ],
}
