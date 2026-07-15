"""
Curated public BDC universe for the BDC Value Map.

Ticker/CIK/legal-name values are grounded in SEC public ticker data. Peer
groups and manager classifications are intentionally kept here, away from UI
code, so they can be reviewed as market structures change.
"""

PEER_DIVERSIFIED = "Diversified middle-market"
PEER_UPPER_MM = "Upper-middle-market/direct lending"
PEER_VENTURE = "Venture/technology lending"
PEER_INTERNAL = "Internally managed"
PEER_SPECIALTY = "Specialty or equity-heavy"

PEER_GROUPS = [
    PEER_DIVERSIFIED,
    PEER_UPPER_MM,
    PEER_VENTURE,
    PEER_INTERNAL,
    PEER_SPECIALTY,
]


BDC_UNIVERSE = [
    {"ticker": "ARCC", "legalName": "ARES CAPITAL CORP", "cik": "0001287750", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "Ares Capital Management LLC", "specialty": None},
    {"ticker": "BBDC", "legalName": "Barings BDC, Inc.", "cik": "0001379785", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "Barings LLC", "specialty": None},
    {"ticker": "BCSF", "legalName": "Bain Capital Specialty Finance, Inc.", "cik": "0001655050", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "Bain Capital Credit", "specialty": None},
    {"ticker": "BXSL", "legalName": "Blackstone Secured Lending Fund", "cik": "0001736035", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "Blackstone Credit BDC Advisors LLC", "specialty": None},
    {"ticker": "CCAP", "legalName": "Crescent Capital BDC, Inc.", "cik": "0001633336", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "Crescent Cap Advisors, LLC", "specialty": None},
    {"ticker": "CGBD", "legalName": "Carlyle Secured Lending, Inc.", "cik": "0001544206", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "Carlyle Global Credit Investment Management L.L.C.", "specialty": None},
    {"ticker": "CION", "legalName": "CION Investment Corp", "cik": "0001534254", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "CION Investment Management, LLC", "specialty": None},
    {"ticker": "CSWC", "legalName": "CAPITAL SOUTHWEST CORP", "cik": "0000017313", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_INTERNAL, "managementStructure": "Internally managed", "managerName": None, "specialty": None},
    {"ticker": "FDUS", "legalName": "FIDUS INVESTMENT Corp", "cik": "0001513363", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "Fidus Investment Advisors, LLC", "specialty": None},
    {"ticker": "FSK", "legalName": "FS KKR Capital Corp", "cik": "0001422183", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "FS/KKR Advisor, LLC", "specialty": None},
    {"ticker": "GAIN", "legalName": "GLADSTONE INVESTMENT CORPORATION", "cik": "0001321741", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_SPECIALTY, "managementStructure": "Externally managed", "managerName": "Gladstone Management Corporation", "specialty": "Buyouts and equity co-investments"},
    {"ticker": "GBDC", "legalName": "GOLUB CAPITAL BDC, Inc.", "cik": "0001476765", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "GC Advisors LLC", "specialty": None},
    {"ticker": "GECC", "legalName": "Great Elm Capital Corp.", "cik": "0001675033", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_SPECIALTY, "managementStructure": "Externally managed", "managerName": "Great Elm Capital Management, Inc.", "specialty": "Specialty credit"},
    {"ticker": "GLAD", "legalName": "GLADSTONE CAPITAL CORP", "cik": "0001143513", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "Gladstone Management Corporation", "specialty": None},
    {"ticker": "GSBD", "legalName": "Goldman Sachs BDC, Inc.", "cik": "0001572694", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "Goldman Sachs Asset Management, L.P.", "specialty": None},
    {"ticker": "HRZN", "legalName": "Horizon Technology Finance Corp", "cik": "0001487428", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_VENTURE, "managementStructure": "Externally managed", "managerName": "Horizon Technology Finance Management LLC", "specialty": "Venture lending"},
    {"ticker": "HTGC", "legalName": "Hercules Capital, Inc.", "cik": "0001280784", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_INTERNAL, "managementStructure": "Internally managed", "managerName": None, "specialty": "Venture and technology lending"},
    {"ticker": "ICMB", "legalName": "Investcorp Credit Management BDC, Inc.", "cik": "0001578348", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "Investcorp Credit Management BDC, Inc.", "specialty": None},
    {"ticker": "LIEN", "legalName": "Chicago Atlantic BDC, Inc.", "cik": "0001843162", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_SPECIALTY, "managementStructure": "Externally managed", "managerName": "Chicago Atlantic BDC Advisers, LLC", "specialty": "Asset-based specialty finance"},
    {"ticker": "MAIN", "legalName": "Main Street Capital CORP", "cik": "0001396440", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_INTERNAL, "managementStructure": "Internally managed", "managerName": None, "specialty": "Lower middle-market and equity co-investments"},
    {"ticker": "MFIC", "legalName": "MidCap Financial Investment Corp", "cik": "0001278752", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "Apollo Investment Management, L.P.", "specialty": None},
    {"ticker": "MRCC", "legalName": "Monroe Capital Corporation", "cik": "0001512931", "exchange": "Nasdaq", "publiclyTraded": False, "active": False, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "Monroe Capital BDC Advisors, LLC", "specialty": "Merged into HRZN on 2026-04-14"},
    {"ticker": "MSDL", "legalName": "Morgan Stanley Direct Lending Fund", "cik": "0001782524", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "MS Capital Partners Adviser Inc.", "specialty": None},
    {"ticker": "MSIF", "legalName": "MSC INCOME FUND, INC.", "cik": "0001535778", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_INTERNAL, "managementStructure": "Internally managed", "managerName": None, "specialty": "Lower middle-market"},
    {"ticker": "NCDL", "legalName": "Nuveen Churchill Direct Lending Corp.", "cik": "0001737924", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "Churchill DLC Advisor LLC", "specialty": None},
    {"ticker": "NMFC", "legalName": "New Mountain Finance Corp", "cik": "0001496099", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "New Mountain Finance Advisers BDC, L.L.C.", "specialty": None},
    {"ticker": "NSLR", "legalName": "Neostellar Capital Corp.", "cik": "0001509470", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_SPECIALTY, "managementStructure": "Externally managed", "managerName": "Neostellar Advisors LLC", "specialty": "Specialty and growth-oriented investments"},
    {"ticker": "OBDC", "legalName": "Blue Owl Capital Corp", "cik": "0001655888", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "Blue Owl Credit Advisors LLC", "specialty": None},
    {"ticker": "OCSL", "legalName": "Oaktree Specialty Lending Corp", "cik": "0001414932", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "Oaktree Fund Advisors, LLC", "specialty": None},
    {"ticker": "OFS", "legalName": "OFS Capital Corp", "cik": "0001487918", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "OFS Capital Management, LLC", "specialty": None},
    {"ticker": "OXSQ", "legalName": "Oxford Square Capital Corp.", "cik": "0001259429", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_SPECIALTY, "managementStructure": "Externally managed", "managerName": "Oxford Square Management, LLC", "specialty": "CLO debt and equity exposure"},
    {"ticker": "PFLT", "legalName": "PennantPark Floating Rate Capital Ltd.", "cik": "0001504619", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "PennantPark Investment Advisers, LLC", "specialty": "Floating-rate senior secured lending"},
    {"ticker": "PFX", "legalName": "PhenixFIN Corp", "cik": "0001490349", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_SPECIALTY, "managementStructure": "Internally managed", "managerName": None, "specialty": "Equity-heavy and opportunistic"},
    {"ticker": "PNNT", "legalName": "PENNANTPARK INVESTMENT CORP", "cik": "0001383414", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "PennantPark Investment Advisers, LLC", "specialty": None},
    {"ticker": "PSBD", "legalName": "Palmer Square Capital BDC Inc.", "cik": "0001794776", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "Palmer Square BDC Advisor LLC", "specialty": None},
    {"ticker": "PSEC", "legalName": "PROSPECT CAPITAL CORP", "cik": "0001287032", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "Prospect Capital Management L.P.", "specialty": None},
    {"ticker": "RAND", "legalName": "RAND CAPITAL CORP", "cik": "0000081955", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_SPECIALTY, "managementStructure": "Externally managed", "managerName": "Rand Capital Management, LLC", "specialty": "Small-cap and equity-oriented investments"},
    {"ticker": "RWAY", "legalName": "Runway Growth Finance Corp.", "cik": "0001653384", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_VENTURE, "managementStructure": "Externally managed", "managerName": "Runway Growth Capital LLC", "specialty": "Venture lending"},
    {"ticker": "SAR", "legalName": "SARATOGA INVESTMENT CORP.", "cik": "0001377936", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "Saratoga Investment Advisors, LLC", "specialty": None},
    {"ticker": "SCM", "legalName": "Stellus Capital Investment Corp", "cik": "0001551901", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "Stellus Capital Management, LLC", "specialty": None},
    {"ticker": "SLRC", "legalName": "SLR Investment Corp.", "cik": "0001418076", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "SLR Capital Partners, LLC", "specialty": None},
    {"ticker": "TCPC", "legalName": "BlackRock TCP Capital Corp.", "cik": "0001370755", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "Tennenbaum Capital Partners, LLC", "specialty": None},
    {"ticker": "TPVG", "legalName": "TriplePoint Venture Growth BDC Corp.", "cik": "0001580345", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_VENTURE, "managementStructure": "Externally managed", "managerName": "TriplePoint Advisers LLC", "specialty": "Venture lending"},
    {"ticker": "TRIN", "legalName": "Trinity Capital Inc.", "cik": "0001786108", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_VENTURE, "managementStructure": "Internally managed", "managerName": None, "specialty": "Venture lending and equipment finance"},
    {"ticker": "TSLX", "legalName": "Sixth Street Specialty Lending, Inc.", "cik": "0001508655", "exchange": "NYSE", "publiclyTraded": True, "active": True, "peerGroup": PEER_UPPER_MM, "managementStructure": "Externally managed", "managerName": "Sixth Street Specialty Lending Advisers, LLC", "specialty": None},
    {"ticker": "WHF", "legalName": "WhiteHorse Finance, Inc.", "cik": "0001552198", "exchange": "Nasdaq", "publiclyTraded": True, "active": True, "peerGroup": PEER_DIVERSIFIED, "managementStructure": "Externally managed", "managerName": "H.I.G. WhiteHorse Advisers, LLC", "specialty": None},
]


def active_bdc_universe():
    return [row for row in BDC_UNIVERSE if row["publiclyTraded"] and row["active"]]


def universe_by_ticker():
    return {row["ticker"]: row for row in BDC_UNIVERSE}
