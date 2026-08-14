// Entry point: renders CV from _cv_data.json (staging file).
// Human-readable document title — the browser PDF viewer shows this instead
// of the blob URL. Does not affect the backend's id-based file tracking.
#set document(title: "CV")
#import "render.typ": render
#render("_cv_data.json")
