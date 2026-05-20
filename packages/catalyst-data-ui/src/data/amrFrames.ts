/**
 * Hand-curated dictionary of PropBank AMR frames most-seen on our
 * extraction output. Lets the UI render a short human-readable gloss
 * next to the frame id (``have-org-role-91`` → "X holds role Y in
 * org Z") instead of forcing the reader to know PropBank.
 *
 * Source: PropBank frame files (https://propbank.github.io/) and the
 * congress + media label packs in tree at
 * ``catalyst-data/k8s/base/<domain>/prompts/<pack>.labels.yaml``.
 *
 * Not exhaustive — when an AMR frame isn't here, the badge falls back
 * to showing just the frame id (still useful — most PropBank ids are
 * self-explanatory verbs).
 */

export interface AmrFrame {
  /** Short one-line description; appears next to the frame badge. */
  gloss: string;
  /** Optional: structured role labels (ARG0=agent, ARG1=patient, ...)
   *  so the panel can label the role mapping table. */
  roles?: Record<string, string>;
}

/** Common PropBank frames seen across congress + media corpora. Keep
 *  glosses tight — the panel renders these in a small monospaced box. */
export const AMR_FRAMES: Record<string, AmrFrame> = {
  // ── Action / event frames ──────────────────────────────────────
  "say-01": {
    gloss: "speaker says proposition",
    roles: { ARG0: "speaker", ARG1: "utterance", ARG2: "hearer" },
  },
  "tell-01": {
    gloss: "speaker tells hearer proposition",
    roles: { ARG0: "speaker", ARG1: "proposition", ARG2: "hearer" },
  },
  "elect-01": {
    gloss: "electorate elects candidate to role",
    roles: { ARG0: "electorate", ARG1: "candidate", ARG2: "role" },
  },
  "appoint-01": {
    gloss: "appointer appoints appointee to role",
    roles: { ARG0: "appointer", ARG1: "appointee", ARG2: "role" },
  },
  "choose-01": {
    gloss: "chooser chooses chosen",
    roles: { ARG0: "chooser", ARG1: "chosen", ARG2: "choice-set" },
  },
  "pass-03": {
    gloss: "legislature passes bill",
    roles: { ARG0: "legislature", ARG1: "bill" },
  },
  "vote-01": {
    gloss: "voter votes on issue",
    roles: { ARG0: "voter", ARG1: "issue", ARG2: "choice" },
  },
  "introduce-01": {
    gloss: "introducer presents item to audience",
    roles: { ARG0: "introducer", ARG1: "item-introduced", ARG2: "audience" },
  },
  "support-01": {
    gloss: "supporter supports cause/entity",
    roles: { ARG0: "supporter", ARG1: "thing-supported" },
  },
  "oppose-01": {
    gloss: "opponent opposes target",
    roles: { ARG0: "opponent", ARG1: "thing-opposed" },
  },
  "amend-01": {
    gloss: "amender amends thing",
    roles: { ARG0: "amender", ARG1: "thing-amended", ARG2: "new-form" },
  },
  "give-01": {
    gloss: "giver gives gift to recipient",
    roles: { ARG0: "giver", ARG1: "gift", ARG2: "recipient" },
  },
  "hope-01": {
    gloss: "hoper hopes for outcome",
    roles: { ARG0: "hoper", ARG1: "outcome-hoped-for" },
  },
  "state-01": {
    gloss: "agent states proposition",
    roles: { ARG0: "agent", ARG1: "proposition" },
  },

  // ── Relation frames (-91 = nominalised relations) ──────────────
  "have-org-role-91": {
    gloss: "X holds role Y in organisation Z",
    roles: { ARG0: "person", ARG1: "organisation", ARG2: "role", ARG3: "position" },
  },
  "have-rel-role-91": {
    gloss: "X has relation Y to Z",
    roles: {
      ARG0: "person-a",
      ARG1: "person-b",
      ARG2: "relationship",
      ARG3: "role-a",
      ARG4: "role-b",
    },
  },
  "have-degree-91": {
    gloss: "X has degree Y of attribute Z",
    roles: { ARG1: "domain", ARG2: "attribute", ARG3: "degree", ARG4: "compared-to" },
  },
  "have-quant-91": {
    gloss: "X has quantity Y",
    roles: { ARG1: "domain", ARG2: "quantity" },
  },
  "contrast-01": {
    gloss: "first claim contrasts with second",
    roles: { ARG1: "first-clause", ARG2: "second-clause" },
  },

  // ── Structured-extraction predicates (not AMR; the structured
  //    pipeline uses a small closed vocab — list them so the panel
  //    can still gloss the relation when reading congress
  //    structured assertions). ─────────────────────────────────────
  co_sponsors: {
    gloss: "person formally co-sponsors a bill",
    roles: { ARG0: "cosponsor", ARG1: "bill" },
  },
  sponsors: {
    gloss: "person formally sponsors a bill",
    roles: { ARG0: "sponsor", ARG1: "bill" },
  },
  became_public_law_at: {
    gloss: "bill became public law on date",
    roles: { ARG0: "bill", ARG1: "date" },
  },
  member_of: {
    gloss: "person is a member of organisation",
    roles: { ARG0: "member", ARG1: "organisation" },
  },
};

/** Look up a frame gloss + roles. Strips a leading ``NOVEL_`` prefix
 *  (which the projector adds to predicates whose frame isn't in the
 *  active label pack — the underlying frame id is still informative). */
export function lookupFrame(idOrPredicate: string | null | undefined): AmrFrame | null {
  if (!idOrPredicate) return null;
  const trimmed = idOrPredicate.replace(/^NOVEL_/, "");
  return AMR_FRAMES[trimmed] ?? AMR_FRAMES[idOrPredicate] ?? null;
}
