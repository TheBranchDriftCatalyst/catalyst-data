# Feature Request: Financial Disclosure & Congressional Finance Data Sources

## Summary

Ingest structured financial disclosure data — congressional stock trades, lobbying registrations, campaign finance filings, corporate beneficial ownership records, and SEC disclosures — as first-class data sources. These provide entity-rich, timestamped financial activity records that cross-reference directly against entities already in the knowledge graph from congressional and leaked document corpora.

## Motivation

The existing congress-data and open-leaks pipelines extract entities (legislators, corporations, shell companies) and assertions ("Entity X sponsors Bill Y", "Shell company X owns Entity Y") from text. Financial disclosure data provides **ground-truth transactional records** that can:

1. **Validate or contradict** text-derived assertions with official filings
2. **Enrich entity profiles** with financial relationships not mentioned in documents
3. **Surface temporal correlations** — stock trades around committee hearings, lobbying spend before legislation, donations preceding votes
4. **Bridge domains** — the same entities appear across congressional records, leaked documents, and financial filings, enabling cross-source concordance at scale

## Data Sources

### Congressional Financial Disclosures

| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [Senate eFD](https://efdsearch.senate.gov/) | Senator financial disclosures (assets, transactions, liabilities, gifts) | HTML/PDF, periodic transaction reports | Free, public |
| [House Financial Disclosures](https://disclosures-clerk.house.gov/FinancialDisclosure) | House member financial disclosures | PDF/XML | Free, public |
| [Capitol Trades](https://www.capitoltrades.com/) | Aggregated congressional stock trades | API/CSV | Free tier + paid |
| [Quiver Quantitative](https://www.quiverquant.com/) | Congressional trading, lobbying, contracts | API | Free tier + paid |
| [Senate STOCK Act eFilings](https://efdsearch.senate.gov/search/home/) | Periodic transaction reports (PTRs) — individual stock trades within 45 days | XML/HTML | Free, public |

**Key fields:** filer name, transaction date, asset description, ticker symbol, transaction type (purchase/sale), amount range, filing date, committee assignments

### Campaign Finance

| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [FEC API](https://api.open.fec.gov/) | Campaign contributions, expenditures, committee filings | REST API, bulk CSV | Free, API key |
| [OpenSecrets API](https://www.opensecrets.org/open-data/api) | Aggregated donor/recipient data, industry coding | REST API | Free, API key |
| [FEC Bulk Data](https://www.fec.gov/data/browse-data/?tab=bulk-data) | Complete filing history (contributions, expenditures, independent expenditures) | CSV/pipe-delimited | Free, bulk download |
| [Follow The Money (NIMSP)](https://www.followthemoney.org/) | State-level campaign finance | API/CSV | Free |

**Key fields:** contributor name/employer/occupation, recipient committee, amount, date, election cycle, contribution type, donor ZIP code

### Lobbying

| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [Senate Lobbying Disclosures (LDA)](https://lda.senate.gov/) | Lobbying registrations and quarterly reports | XML/CSV | Free, public |
| [House Lobbying Disclosures](https://lobbyingdisclosure.house.gov/) | Same filings, House portal | XML | Free, public |
| [OpenSecrets Lobbying](https://www.opensecrets.org/federal-lobbying) | Aggregated lobbying data with industry coding | API | Free, API key |

**Key fields:** registrant (lobbying firm), client, lobbyist names, issue areas, specific bills lobbied, income/expenses, government entities contacted, foreign entity flag

### Corporate Ownership & SEC

| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [SEC EDGAR Full-Text Search](https://efts.sec.gov/LATEST/search-index) | All SEC filings (10-K, 10-Q, 8-K, 13F, proxy statements) | XBRL/HTML/XML | Free, public |
| [SEC EDGAR Company API](https://www.sec.gov/search) | Company filings, CIK lookup | REST API | Free |
| [SEC 13F Institutional Holdings](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=13F) | Quarterly institutional stock holdings | XML/CSV | Free |
| [OpenCorporates](https://opencorporates.com/) | Global corporate registry data | API | Free tier + paid |
| [ICIJ Offshore Leaks DB](https://offshoreleaks.icij.org/) | Panama Papers, Paradise Papers, Pandora Papers entity data | CSV/Neo4j dump | Free, public |
| [FinCEN Beneficial Ownership](https://www.fincen.gov/beneficial-ownership-information-reporting) | US beneficial ownership (BOI) filings (2024+) | Restricted API | Law enforcement / authorized |
| [UK Companies House](https://www.gov.uk/government/organisations/companies-house) | UK corporate registry + PSC (persons of significant control) | REST API | Free |

**Key fields:** company name, CIK/company number, officers/directors, beneficial owners, filing type, filing date, jurisdiction, parent/subsidiary relationships

### International Financial Disclosures

| Source | Content | Format | Access |
|--------|---------|--------|--------|
| [World Bank STEP](https://www.worldbank.org/en/about/unit/sanctions-system) | Sanctioned/debarred entities | CSV | Free |
| [OFAC SDN List](https://sanctionssearch.ofac.treas.gov/) | US sanctions list (persons, entities, vessels) | XML/CSV | Free |
| [EU Sanctions Map](https://www.sanctionsmap.eu/) | EU sanctions regimes | API | Free |
| [FATF Country Reports](https://www.fatf-gafi.org/) | AML/CFT assessments | PDF | Free |

## Data Model

### Financial Transaction (bronze)

```python
@dataclass
class FinancialTransaction:
    """Raw financial transaction from disclosure filings."""
    source: str               # "senate_efd", "fec", "sec_13f", etc.
    filer_name: str
    filer_id: str | None      # bioguide_id, CIK, FEC committee ID
    transaction_date: date
    filing_date: date
    transaction_type: str     # "purchase", "sale", "contribution", "expenditure", "lobbying_income"
    asset_or_recipient: str   # ticker/company name (trades), recipient name (donations)
    amount_low: float | None  # range lower bound (disclosures use ranges)
    amount_high: float | None # range upper bound
    amount_exact: float | None # exact amount when available (FEC)
    metadata: dict            # source-specific fields (committee, industry_code, issue_area, etc.)
    raw_filing_url: str | None
```

### Disclosure Filing (bronze)

```python
@dataclass
class DisclosureFiling:
    """A single disclosure document (PTR, quarterly lobbying report, FEC filing)."""
    filing_id: str
    source: str
    filer_name: str
    filer_id: str | None
    filing_type: str          # "periodic_transaction_report", "quarterly_lobbying", "form_13f", etc.
    filing_date: date
    period_start: date | None
    period_end: date | None
    document_url: str | None
    transactions: list[str]   # transaction_ids
```

### Financial Relationship (silver)

```python
@dataclass
class FinancialRelationship:
    """Derived relationship between two entities via financial activity."""
    relationship_id: str
    source_entity: str        # filer/donor/lobbyist
    target_entity: str        # asset/recipient/client
    relationship_type: str    # "trades_stock_of", "donates_to", "lobbies_for", "holds_shares_in", "officer_of"
    total_amount: float | None
    transaction_count: int
    first_observed: date
    last_observed: date
    source_filings: list[str] # filing_ids
```

### Financial Assertion (gold)

```python
@dataclass
class FinancialAssertion:
    """Assertion derived from financial disclosure data."""
    # Maps to the existing Assertion model with financial-specific qualifiers
    subject_text: str         # "Sen. Jane Smith"
    predicate: str            # "purchased_stock_of"
    object_text: str          # "Acme Corp (ACME)"
    confidence: float         # 1.0 for direct filings, lower for inferred
    qualifiers: dict          # {"time": "2025-03-01", "amount": "$15,001-$50,000",
                              #  "source_attribution": "Senate PTR filing 2025-03-15",
                              #  "committee_assignments": ["Finance", "Armed Services"]}
    provenance: Provenance    # links to raw filing
```

## Pipeline Design

```
Bronze                         Silver                          Gold                        Platinum
──────                         ──────                          ────                        ────────
senate_disclosures         →  congressional_trades         →  financial_assertions     →  financial_entity_graph
house_disclosures          →  financial_relationships      →  temporal_correlations       (cross-ref with KG)
fec_contributions          →  filer_entities                  (trade ↔ committee hearing
fec_expenditures              (entity resolution)              timing analysis)
lobbying_registrations     →  lobbying_relationships
lobbying_reports
sec_13f_holdings           →  corporate_relationships
sec_insider_transactions
ofac_sanctions_list        →  sanctions_entities
```

### Asset Breakdown

| Asset | Layer | Description |
|-------|-------|-------------|
| `senate_disclosures` | bronze | Scrape/parse Senate eFD periodic transaction reports |
| `house_disclosures` | bronze | Scrape/parse House financial disclosures |
| `fec_contributions` | bronze | Ingest FEC individual/committee contributions via API |
| `fec_expenditures` | bronze | Ingest FEC disbursements and independent expenditures |
| `lobbying_registrations` | bronze | Parse LDA registration XML |
| `lobbying_reports` | bronze | Parse LDA quarterly activity reports |
| `sec_13f_holdings` | bronze | Ingest 13F institutional holdings from EDGAR |
| `sec_insider_transactions` | bronze | Ingest Form 4 insider trading filings |
| `ofac_sanctions` | bronze | Ingest OFAC SDN list |
| `congressional_trades` | silver | Normalize and deduplicate congressional stock trades |
| `financial_relationships` | silver | Derive entity-to-entity financial edges |
| `filer_entities` | silver | Resolve filer identities to canonical entities (bioguide_id, CIK) |
| `lobbying_relationships` | silver | Map lobbyist → client → issue → bill relationships |
| `corporate_relationships` | silver | Officer/director/subsidiary relationships from SEC filings |
| `financial_assertions` | gold | Generate qualified assertions from financial records |
| `temporal_correlations` | gold | Detect trade ↔ hearing, donation ↔ vote temporal patterns |
| `sanctions_cross_ref` | gold | Match KG entities against sanctions lists |
| `financial_entity_graph` | platinum | Link financial entities to canonical KG entities |

## Cross-Reference Use Cases

### 1. Trade-Hearing Correlation
Detect stock trades by committee members in companies appearing before their committee within a configurable window (e.g., 30 days). Generates assertions: "Sen. X purchased stock in Company Y 14 days before Company Y testified before Committee Z."

### 2. Lobbying-Legislation Bridge
Link lobbying clients to specific bills lobbied, then cross-reference against congressional votes and sponsorships. "Lobbying firm A, paid $2.1M by Company B, lobbied on H.R. 1234, which was sponsored by Rep. C who received $50,000 from Company B's PAC."

### 3. Offshore Entity Linking
Cross-reference ICIJ Offshore Leaks entities (already in open-leaks pipeline) with SEC beneficial ownership filings and congressional financial disclosures. Surfaces hidden connections between public officials and offshore structures.

### 4. Sanctions Screening
Continuous matching of KG entities against OFAC/EU sanctions lists. Any new entity entering the graph is screened; any entity already in the graph is re-screened when sanctions lists update.

### 5. Donor Network Analysis
Map donor networks from FEC data as graph edges, detect bundling patterns, and cross-reference donor employers with entities mentioned in congressional hearing transcripts.

### 6. Insider Trading Detection
Correlate SEC Form 4 insider transactions with upcoming earnings, FDA decisions, or government contract awards mentioned in congressional records.

## Entity Linking Strategy

Financial data has **strong identifiers** that make entity linking more tractable than free text:

| Identifier | Source | Stability |
|------------|--------|-----------|
| Bioguide ID | Congress.gov | Permanent per legislator |
| FEC Committee ID | FEC | Permanent per committee |
| CIK | SEC EDGAR | Permanent per company |
| Ticker Symbol | Exchanges | Semi-stable (changes on rename/merger) |
| CUSIP/ISIN | DTCC/ANNA | Permanent per security |
| EIN | IRS | Permanent per entity |
| LEI | GLEIF | Permanent per legal entity |

These should be stored as `external_ids` on `CanonicalEntity` and used as high-confidence concordance signals (exact ID match = 0.99 confidence sameAs).

## K8s Considerations

### New Package

```
packages/
  financial-disclosures/
    src/financial_disclosures/
      __init__.py
      assets/
        senate_disclosures.py
        house_disclosures.py
        fec_contributions.py
        lobbying.py
        sec_filings.py
        sanctions.py
        financial_assertions.py
        temporal_correlations.py
      client/
        fec_client.py
        edgar_client.py
        efd_scraper.py
        lda_parser.py
        ofac_client.py
    prompts/
      financial-assertion.prompt
      temporal-correlation.prompt
    pyproject.toml
```

### Secrets

```yaml
# k8s/secrets/externalsecret-financial.yaml
FEC_API_KEY: ...
OPENSECRETS_API_KEY: ...
OPENCORPORATES_API_KEY: ...  # if using paid tier
```

### Storage Estimates

| Data Type | Volume | Storage |
|-----------|--------|---------|
| Congressional trades (all time) | ~30K transactions | ~10MB |
| FEC contributions (per cycle) | ~20M records | ~5GB |
| Lobbying reports (per year) | ~80K filings | ~2GB |
| SEC 13F holdings (per quarter) | ~5M holdings | ~1GB |
| Derived assertions | ~500K | ~200MB |

Modest storage requirements — financial disclosure data is small compared to AIS/ADS-B.

## Dependencies

- Existing congress-data pipeline (for legislator entity linking via bioguide_id)
- Existing open-leaks pipeline (for offshore entity cross-reference)
- Knowledge-graph package (for canonical entity resolution)
- **No spatial infrastructure required** (unlike AIS/ADS-B) — this can proceed independently

## Priority

**High** — Can be implemented immediately without spatial prerequisites. High cross-reference value with existing congress-data and open-leaks corpora. Strong entity identifiers make concordance reliable.

## References

- [STOCK Act (2012)](https://www.congress.gov/bill/112th-congress/senate-bill/2038)
- [Lobbying Disclosure Act (1995)](https://lobbyingdisclosure.house.gov/lda.html)
- [SEC EDGAR Developer Resources](https://www.sec.gov/search#/dateRange=custom)
- [FEC API Documentation](https://api.open.fec.gov/developers/)
- [OpenSecrets API Documentation](https://www.opensecrets.org/open-data/api-documentation)
- ONTOLOGY.md Sections 4.3 (Qualified Assertions), 5 (Entity Concordance), 9.3 (LLM Extraction)
