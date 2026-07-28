#let render_cover_letter(data) = {
  let first = data.at("first_name", default: "")
  let last = data.at("last_name", default: "")
  let email = data.at("email", default: "")
  let phone = data.at("phone", default: none)
  let location = data.at("location", default: none)
  let cl = data.at("cover_letter", default: none)

  set page(
    paper: "a4",
    margin: (top: 1in, bottom: 1in, left: 1in, right: 1in),
    numbering: none,
  )
  set text(font: "Latin Modern Roman", size: 10.5pt)
  set par(leading: 0.55em, justify: false)

  if cl == none {
    text("No cover letter provided.")
    return
  }

  let opening = cl.at("opening_paragraph", default: "")
  let body = cl.at("body_paragraphs", default: ())
  let company = cl.at("company_connection_paragraph", default: none)
  let closing = cl.at("closing_paragraph", default: "")

  // ── Sender info ────────────────────────────────────────────
  if location != none { text(location) }
  if phone != none { text(phone) }
  text(email)
  v(0.3em)

  // ── Date ───────────────────────────────────────────────────
  let now = datetime.today()
  text(now.display("[day] [month repr:long] [year]"))
  v(0.6em)

  // ── Recipient ──────────────────────────────────────────────
  text("Hiring Manager")
  v(0.6em)

  // ── Salutation ─────────────────────────────────────────────
  if opening != "" {
    text(opening)
    v(0.3em)
  }

  // ── Body ───────────────────────────────────────────────────
  for para in body {
    text(para)
    v(0.3em)
  }

  if company != none and company != "" {
    text(company)
    v(0.3em)
  }

  // ── Closing ────────────────────────────────────────────────
  if closing != "" {
    v(0.15em)
    text(closing)
  }

  v(0.6em)
  text("Sincerely,")
  v(0.6em)
  text(weight: "bold", first + " " + last)
}
