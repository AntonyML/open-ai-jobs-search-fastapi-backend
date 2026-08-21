// ── Harvard / ATS Resume Template ─────────────────────────────────
// Single column, chronological reverse, clean serif (Latin Modern).
// Calibrated for elegant density (1 page standard for modern resumes).

#let _months_en = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
#let _months_es = ("Ene", "Feb", "Mar", "Abr", "May", "Jun",
                   "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")

#let _fmt_date(iso, is_es: false) = {
  if iso == none or iso == "" { return none }
  if type(iso) == str and (iso == "Present" or lower(iso) == "present" or lower(iso) == "presente") {
    return if is_es { "Presente" } else { "Present" }
  }
  let parts = if type(iso) == str { iso.split("-") } else { return none }
  if parts.len() < 2 { return parts.at(0) }
  let m = int(parts.at(1))
  let months = if is_es { _months_es } else { _months_en }
  let mn = if m >= 1 and m <= 12 { months.at(m - 1) } else { parts.at(1) }
  return mn + " " + parts.at(0)
}

#let fmt_date_range(dr, is_es: false) = {
  let start = dr.at("start", default: none)
  let end = dr.at("end", default: none)
  let sf = _fmt_date(start, is_es: is_es)
  let ef = _fmt_date(end, is_es: is_es)
  let present_label = if is_es { "Presente" } else { "Present" }
  if sf == none and ef == none { return "" }
  if sf == none { return ef }
  if ef == none or ef == "" { return sf + " – " + present_label }
  if type(ef) == str and (lower(ef) == "present" or lower(ef) == "presente") { return sf + " – " + present_label }
  if ef == sf { return sf }
  return sf + " – " + ef
}

#let _contact_strip(url) = {
  let u = url.replace("https://", "").replace("http://", "").replace("www.", "")
  if u.ends-with("/") { u.slice(0, u.len() - 1) } else { u }
}

#let _section-header(title) = {
  v(0.18em)
  text(weight: "bold", size: 9.8pt, upper(title))
  v(-0.25em)
  line(length: 100%, stroke: 0.4pt + rgb("#333333"))
  v(0.04em)
}

#let _hanging-bullet(text-body) = {
  let indent = 1.3em
  let hang = 0.35em
  let bullet-box = box(width: indent - hang, [\u{2022} #h(0.1em)])
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
  let rc = if type(right-content) == array {
    if right-content.len() > 0 { str(right-content.at(0)) } else { "" }
  } else {
    str(right-content)
  }
  let sep = if rc != "" { " — " } else { "" }
  [#left-content #text(fill: rgb("#444"), size: 9pt, sep + rc)]
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
  let lang = data.at("language", default: "en")
  let is_es = lang == "es" or lang == "es-ES" or lang == "spanish" or lang == "Spanish"

  set page(
    paper: "a4",
    margin: (top: 0.38in, bottom: 0.38in, left: 0.45in, right: 0.45in),
    header-ascent: 0cm,
    numbering: none,
  )
  set text(font: "Latin Modern Roman", size: 9.3pt)
  set par(leading: 0.28em, justify: false)

  let muted = rgb("#444444")

  // ── Header: Name + Contact Line (Clean & Compact) ───────────
  set align(center)
  text(weight: "bold", size: 15.5pt, first + " " + last)
  v(0.04em)

  let contact-parts = ()
  if location != none and location != "" { contact-parts.push(location) }
  if email != "" { contact-parts.push(email) }
  if phone != none and phone != "" { contact-parts.push(phone) }
  if linkedin != none and linkedin != "" { contact-parts.push(_contact_strip(linkedin)) }
  if github != none and github != "" { contact-parts.push(_contact_strip(github)) }
  if portfolio != none and portfolio != "" { contact-parts.push(_contact_strip(portfolio)) }

  if contact-parts.len() > 0 {
    text(fill: muted, size: 8.5pt, contact-parts.join("  |  "))
  }
  v(0.06em)

  set align(left)

  // ── Profile Statement ───────────────────────────────────────
  if profile != none and profile != "" {
    text(size: 9.2pt, fill: muted, profile)
    v(0.04em)
  }

  // ── Experience ──────────────────────────────────────────────
  if experience.len() > 0 {
    _section-header(if is_es { "Experiencia Laboral" } else { "Experience" })
    for entry in experience {
      let title = entry.at("title", default: "")
      let company = entry.at("company", default: "")
      let loc = entry.at("location", default: none)
      let dr = entry.at("date_range", default: (:))
      let bullets = entry.at("bullets", default: ())

      // Line 1: Company — Location
      if company != "" {
        let loc-str = if loc != none and loc != "" { " — " + loc } else { "" }
        text(weight: "bold", size: 9.8pt, company + loc-str)
        linebreak()
      }

      // Line 2: Role | Date range
      let dstr = fmt_date_range(dr, is_es: is_es)
      if title != "" {
        let date-part = if dstr != "" { " | " + dstr } else { "" }
        text(style: "italic", size: 9.2pt, title + date-part)
        linebreak()
      } else if dstr != "" {
        text(size: 9.2pt, dstr)
        linebreak()
      }

      v(0.01em)

      // Bullets
      for bullet in bullets {
        _hanging-bullet(text(size: 9pt, bullet))
        v(0.02em)
      }
      v(0.08em)
    }
  }

  // ── Education ───────────────────────────────────────────────
  if education != none and education.len() > 0 {
    _section-header(if is_es { "Educación y Formación" } else { "Education" })
    for edu in education {
      let degree = edu.at("degree", default: "")
      let institution = edu.at("institution", default: "")
      let period = edu.at("period", default: none)
      let dr = edu.at("date_range", default: (:))
      let topics = edu.at("key_topics", default: none)
      let dstr = if period != none { period } else { fmt_date_range(dr, is_es: is_es) }

      // Line: Degree, Institution + Date
      _entry-line(
        text(weight: "bold", size: 9.5pt, degree + ", " + institution),
        dstr
      )

      let topics-text = if type(topics) == array {
        topics.map(t => str(t)).join(", ")
      } else {
        topics
      }
      if type(topics-text) == str and topics-text != "" {
        text(size: 8.8pt, fill: muted, topics-text)
        linebreak()
      }
      v(0.06em)
    }
  }

  // ── Skills ──────────────────────────────────────────────────
  if skills != none and skills.len() > 0 {
    _section-header(if is_es { "Habilidades y Competencias" } else { "Skills" })
    for group in skills {
      let label = group.at("label", default: "")
      let items = group.at("skills", default: ())
      if items.len() > 0 {
        let parts = ()
        for skill in items {
          parts.push(skill.at("name", default: ""))
        }
        text(weight: "bold", size: 9.2pt, label + ": ")
        text(size: 9.2pt, parts.join(", "))
        linebreak()
      }
    }
    v(0.04em)
  }

  // ── Core Competencies ───────────────────────────────────────
  if competencies != none and competencies.len() > 0 {
    _section-header(if is_es { "Competencias Clave" } else { "Core Competencies" })
    text(size: 9.2pt, fill: muted, competencies.join("  \u{2022}  "))
    v(0.04em)
  }

  // ── Projects / Highlighted Works ────────────────────────────
  if projects != none and projects.len() > 0 {
    _section-header(if is_es { "Proyectos y Trabajos Destacados" } else { "Projects & Highlighted Works" })
    for proj in projects {
      let pname = proj.at("name", default: "")
      let purl = proj.at("url", default: none)
      let pdesc = proj.at("description", default: none)
      let techs = proj.at("technologies", default: none)

      _entry-line(
        text(weight: "bold", size: 9.4pt, pname),
        if purl != none { _contact_strip(purl) } else { "" }
      )

      if pdesc != none and pdesc != "" {
        text(size: 9pt, pdesc)
        linebreak()
      }
      if techs != none and techs.len() > 0 {
        text(size: 8.8pt, fill: muted, (if is_es { "Herramientas/Skills: " } else { "Tech: " }) + techs.join(", "))
        linebreak()
      }
      v(0.05em)
    }
  }

  // ── Certifications ─────────────────────────────────────────
  if certifications != none and certifications.len() > 0 {
    _section-header(if is_es { "Certificaciones y Licencias" } else { "Certifications & Licenses" })
    for cert in certifications {
      let cname = cert.at("name", default: "")
      let issuer = cert.at("issuer", default: "")
      let year = cert.at("year", default: none)

      _entry-line(
        text(weight: "bold", size: 9.2pt, cname + if issuer != "" { ", " + issuer } else { "" }),
        if year != none { year } else { "" }
      )
      v(0.02em)
    }
  }

  // ── Publications ────────────────────────────────────────────
  if publications != none and publications.len() > 0 {
    _section-header(if is_es { "Publicaciones" } else { "Publications" })
    for pub in publications {
      let authors = pub.at("authors", default: "")
      let year = pub.at("year", default: "")
      let ptitle = pub.at("title", default: "")
      let journal = pub.at("journal", default: none)
      let body = authors + " (" + year + "). " + ptitle + "."
      if journal != none and journal != "" {
        body += " " + journal + "."
      }
      text(size: 9pt, body)
      linebreak()
      v(0.04em)
    }
  }

  // ── Awards ──────────────────────────────────────────────────
  if awards != none and awards.len() > 0 {
    _section-header(if is_es { "Reconocimientos y Premios" } else { "Awards" })
    for award in awards {
      let aname = award.at("name", default: "")
      let issuer = award.at("issuer", default: none)
      let year = award.at("year", default: none)

      _entry-line(
        text(weight: "bold", size: 9.2pt, aname + if issuer != none { ", " + issuer } else { "" }),
        if year != none { year } else { "" }
      )
      v(0.04em)
    }
  }
}
