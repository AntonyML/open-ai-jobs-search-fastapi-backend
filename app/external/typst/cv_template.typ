// ── Harvard / ATS Resume Template ─────────────────────────────────
// Single column, chronological reverse, clean serif (Latin Modern).
// Right-aligned dates use fixed spacing — no real columns that break
// ATS reading order.

#let _months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#let _fmt_date(iso) = {
  if iso == none or iso == "" { return none }
  if type(iso) == str and (iso == "Present" or lower(iso) == "present") { return "Present" }
  let parts = if type(iso) == str { iso.split("-") } else { return none }
  if parts.len() < 2 { return parts.at(0) }
  let m = int(parts.at(1))
  let mn = if m >= 1 and m <= 12 { _months.at(m - 1) } else { parts.at(1) }
  return mn + " " + parts.at(0)
}

#let fmt_date_range(dr) = {
  let start = dr.at("start", default: none)
  let end = dr.at("end", default: none)
  let sf = _fmt_date(start)
  let ef = _fmt_date(end)
  if sf == none and ef == none { return "" }
  if sf == none { return ef }
  if ef == none or ef == "" { return sf + " – Present" }
  if type(ef) == str and lower(ef) == "present" { return sf + " – Present" }
  if ef == sf { return sf }
  return sf + " – " + ef
}

#let _fmt_date_range_from_field(val) = {
  // Accepts either a date_range dict or a period string
  if type(val) == "dictionary" { return fmt_date_range(val) }
  if type(val) == "string" { return val }
  return ""
}

#let _contact_strip(url) = {
  url.replace("https://", "").replace("http://", "").replace("www.", "")
}

#let _section-header(title) = {
  v(0.35em)
  text(weight: "bold", size: 11pt, upper(title))
  line(length: 100%, stroke: 0.4pt + black)
  v(0.12em)
}

#let _hanging-bullet(text-body) = {
  let indent = 1.8em
  let hang = 0.45em
  let bullet-box = box(width: indent - hang, [\u{2022} #h(0.15em)])
  let body-text = text(text-body)
  par(
    hanging-indent: hang,
    first-line-indent: -hang,
    [
      #bullet-box#body-text
    ]
  )
}

#let _entry-line(left-content, right-content) = {
  // Inline right-alignment with fixed spacing — preserves ATS reading order
  left-content
  h(1fr, weak: true)
  text(fill: rgb("#444"), size: 9.5pt, right-content)
  linebreak()
}

#let render_cv(data) = {
  let first = data.at("first_name", default: "")
  let last = data.at("last_name", default: "")
  let email = data.at("email", default: "")
  let phone = data.at("phone", default: none)
  let location = data.at("location", default: none)
  let linkedin = data.at("linkedin", default: none)
  let github = data.at("github", default: none)
  let portfolio = data.at("portfolio_url", default: none)
  let profile = data.at("profile_statement", default: none)
  let competencies = data.at("core_competencies", default: none)
  let skills = data.at("skills", default: none)
  let experience = data.at("experience", default: ())
  let projects = data.at("projects", default: none)
  let education = data.at("education", default: none)
  let certifications = data.at("certifications", default: none)
  let publications = data.at("publications", default: none)
  let awards = data.at("awards", default: none)

  set page(
    paper: "a4",
    margin: (top: 0.55in, bottom: 0.5in, left: 0.65in, right: 0.65in),
    header-ascent: 0cm,
    numbering: none,
  )
  set text(font: "Latin Modern Roman", size: 10pt)
  set par(leading: 0.35em, justify: false)

  let muted = rgb("#444444")

  // ── Header: Name + Contact Line ─────────────────────────────
  set align(center)
  text(weight: "bold", size: 17pt, first + " " + last)
  v(0.06em)

  let contact-parts = ()
  if location != none { contact-parts.push(location) }
  if email != "" { contact-parts.push(email) }
  if phone != none { contact-parts.push(phone) }
  if linkedin != none { contact-parts.push(_contact_strip(linkedin)) }
  if github != none { contact-parts.push(_contact_strip(github)) }
  if portfolio != none { contact-parts.push(_contact_strip(portfolio)) }

  if contact-parts.len() > 0 {
    text(fill: muted, size: 9.5pt, contact-parts.join("  |  "))
  }
  v(0.1em)

  set align(left)

  // ── Profile Statement (compact, optional) ───────────────────
  if profile != none and profile != "" {
    v(0.1em)
    text(size: 10pt, fill: muted, profile)
    v(0.05em)
  }

  // ── Experience (FIRST — for experienced candidates) ─────────
  if experience.len() > 0 {
    _section-header("Experience")
    for entry in experience {
      let title = entry.at("title", default: "")
      let company = entry.at("company", default: "")
      let loc = entry.at("location", default: none)
      let dr = entry.at("date_range", default: (:))
      let bullets = entry.at("bullets", default: ())

      // Line 1: Company (bold) + Location (right)
      if company != "" {
        let loc-str = if loc != none { loc } else { "" }
        _entry-line(text(weight: "bold", size: 10.5pt, company), loc-str)
      }

      // Line 2: Title (italic) + Date range (right)
      let dstr = fmt_date_range(dr)
      if title != "" {
        _entry-line(text(style: "italic", size: 10pt, title), dstr)
      } else if dstr != "" {
        _entry-line("", dstr)
      }

      v(0.02em)

      // Bullets
      for bullet in bullets {
        _hanging-bullet(text(size: 9.5pt, bullet))
        v(0.04em)
      }
      v(0.18em)
    }
  }

  // ── Education ───────────────────────────────────────────────
  if education != none and education.len() > 0 {
    _section-header("Education")
    for edu in education {
      let degree = edu.at("degree", default: "")
      let institution = edu.at("institution", default: "")
      let period = edu.at("period", default: none)
      let dr = edu.at("date_range", default: (:))
      let topics = edu.at("key_topics", default: none)
      let dstr = if period != none { period } else { fmt_date_range(dr) }

      // Line: Degree, Institution (bold) + Date (right)
      _entry-line(
        text(weight: "bold", size: 10pt, degree + ", " + institution),
        dstr
      )

      if topics != none and topics != "" {
        text(size: 9.5pt, fill: muted, topics)
        linebreak()
      }
      v(0.15em)
    }
  }

  // ── Skills ──────────────────────────────────────────────────
  if skills != none and skills.len() > 0 {
    _section-header("Skills")
    for group in skills {
      let label = group.at("label", default: "")
      let items = group.at("skills", default: ())
      if items.len() > 0 {
        let parts = ()
        for skill in items {
          parts.push(skill.at("name", default: ""))
        }
        text(weight: "bold", size: 10pt, label + ": ")
        text(size: 10pt, parts.join(", "))
        linebreak()
      }
    }
    v(0.1em)
  }

  // ── Core Competencies (if skills not present) ───────────────
  if competencies != none and competencies.len() > 0 {
    _section-header("Core Competencies")
    text(size: 10pt, fill: muted, competencies.join("  \u{2022}  "))
  }

  // ── Projects ────────────────────────────────────────────────
  if projects != none and projects.len() > 0 {
    _section-header("Projects")
    for proj in projects {
      let pname = proj.at("name", default: "")
      let purl = proj.at("url", default: none)
      let pdesc = proj.at("description", default: none)
      let techs = proj.at("technologies", default: none)

      _entry-line(
        text(weight: "bold", size: 10pt, pname),
        if purl != none { _contact_strip(purl) } else { "" }
      )

      if pdesc != none and pdesc != "" {
        text(size: 9.5pt, pdesc)
        linebreak()
      }
      if techs != none and techs.len() > 0 {
        text(size: 9.5pt, fill: muted, "Tech: " + techs.join(", "))
        linebreak()
      }
      v(0.12em)
    }
  }

  // ── Certifications ─────────────────────────────────────────
  if certifications != none and certifications.len() > 0 {
    _section-header("Certifications")
    for cert in certifications {
      let cname = cert.at("name", default: "")
      let issuer = cert.at("issuer", default: "")
      let year = cert.at("year", default: none)

      _entry-line(
        text(weight: "bold", size: 10pt, cname + if issuer != "" { ", " + issuer } else { "" }),
        if year != none { year } else { "" }
      )
      v(0.12em)
    }
  }

  // ── Publications ────────────────────────────────────────────
  if publications != none and publications.len() > 0 {
    _section-header("Publications")
    for pub in publications {
      let authors = pub.at("authors", default: "")
      let year = pub.at("year", default: "")
      let ptitle = pub.at("title", default: "")
      let journal = pub.at("journal", default: none)
      let body = authors + " (" + year + "). " + ptitle + "."
      if journal != none and journal != "" {
        body += " " + journal + "."
      }
      text(size: 9.5pt, body)
      linebreak()
      v(0.12em)
    }
  }

  // ── Awards ──────────────────────────────────────────────────
  if awards != none and awards.len() > 0 {
    _section-header("Awards")
    for award in awards {
      let aname = award.at("name", default: "")
      let issuer = award.at("issuer", default: none)
      let year = award.at("year", default: none)

      _entry-line(
        text(weight: "bold", size: 10pt, aname + if issuer != none { ", " + issuer } else { "" }),
        if year != none { year } else { "" }
      )
      v(0.15em)
    }
  }
}
