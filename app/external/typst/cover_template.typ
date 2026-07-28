// ── Business Letter Cover Letter Template ─────────────────────────
// Full business letter format: sender info, date, recipient,
// salutation, body, closing. Single column, serif font.

#let _months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#let _fmt_today() = {
  let now = datetime.today()
  let m = int(now.display("[month]"))
  let mn = if m >= 1 and m <= 12 { _months.at(m - 1) } else { str(m) }
  return mn + " " + now.display("[day]") + ", " + now.display("[year]")
}

#let render_cover_letter(data) = {
  let first = data.at("first_name", default: "")
  let last = data.at("last_name", default: "")
  let email = data.at("email", default: "")
  let phone = data.at("phone", default: none)
  let location = data.at("location", default: none)
  let cl = data.at("cover_letter", default: none)

  set page(
    paper: "a4",
    margin: (top: 0.75in, bottom: 0.75in, left: 0.75in, right: 0.75in),
    numbering: none,
  )
  set text(font: "Latin Modern Roman", size: 11pt)
  set par(leading: 0.5em, justify: false)

  if cl == none {
    text("No cover letter provided.")
    return
  }

  let opening = cl.at("opening_paragraph", default: "")
  let body = cl.at("body_paragraphs", default: ())
  let company = cl.at("company_connection_paragraph", default: none)
  let fit = cl.at("personal_fit_paragraph", default: none)
  let closing = cl.at("closing_paragraph", default: "")

  // ── Sender Info ─────────────────────────────────────────────
  text(weight: "bold", first + " " + last)
  if location != none and location != "" { text(location) }
  if phone != none and phone != "" { text(phone) }
  text(email)
  v(0.4em)

  // ── Date ────────────────────────────────────────────────────
  text(_fmt_today())
  v(0.7em)

  // ── Recipient ──────────────────────────────────────────────
  text("Hiring Manager")
  v(0.7em)

  // ── Salutation ─────────────────────────────────────────────
  if opening != "" {
    text(opening)
    v(0.2em)
  } else {
    text("Dear Hiring Manager,")
  }
  v(0.3em)

  // ── Body Paragraphs ─────────────────────────────────────────
  for para in body {
    if para != "" {
      para
      v(0.3em)
    }
  }

  // ── Company Connection ──────────────────────────────────────
  if company != none and company != "" {
    text(company)
    v(0.3em)
  }

  // ── Personal Fit ────────────────────────────────────────────
  if fit != none and fit != "" {
    text(fit)
    v(0.3em)
  }

  // ── Closing ─────────────────────────────────────────────────
  if closing != "" {
    text(closing)
  }

  v(0.5em)
  text("Sincerely,")
  v(0.6em)
  text(weight: "bold", first + " " + last)
}
