// First-run wizard. No inline script (the page ships a strict CSP), no
// third-party assets, and no stored credential is ever rendered back.
"use strict";

var TOKEN_HEADER = "X-SCF-Provision-Token";
var token = "";
var state = { db: { type: "bundled" }, idp: { type: "external_oidc" } };

function $(id) { return document.getElementById(id); }
function byName(name) { return document.querySelector('input[name="' + name + '"]:checked').value; }

function show(step) {
  var panels = document.querySelectorAll(".panel");
  for (var i = 0; i < panels.length; i++) {
    panels[i].hidden = panels[i].getAttribute("data-panel") !== step;
  }
  var items = document.querySelectorAll("#steps li");
  var reached = false;
  for (var j = 0; j < items.length; j++) {
    var name = items[j].getAttribute("data-step");
    if (name === step) { reached = true; items[j].className = "current"; }
    else { items[j].className = reached ? "" : "done"; }
  }
  window.scrollTo(0, 0);
}

function fail(id, message) {
  var node = $(id);
  node.textContent = message;
  node.hidden = false;
}

function clearError(id) { $(id).hidden = true; }

// The token travels in a custom header: that forces a CORS preflight a hostile
// origin cannot pass, and keeps it out of every URL and access log.
function api(path, body) {
  var options = {
    method: body === undefined ? "GET" : "POST",
    headers: { "Accept": "application/json" },
    credentials: "omit"
  };
  options.headers[TOKEN_HEADER] = token;
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  return fetch(path, options).then(function (response) {
    return response.json().catch(function () { return {}; }).then(function (data) {
      return { status: response.status, data: data };
    });
  });
}

function describeError(result) {
  if (result.status === 401) { return "That token was not accepted."; }
  if (result.data && result.data.detail) { return result.data.detail; }
  return "The installer refused the request (HTTP " + result.status + ").";
}

// ---- step 1: token --------------------------------------------------------
$("token-next").addEventListener("click", function () {
  clearError("token-error");
  token = $("token").value.trim();
  if (!token) { fail("token-error", "Paste the token printed by the launcher."); return; }
  this.disabled = true;
  var button = this;
  api("/api/status").then(function (result) {
    button.disabled = false;
    if (result.status !== 200) { fail("token-error", describeError(result)); return; }
    if (result.data.provisioned) {
      fail("token-error", "This secrets directory has already been provisioned.");
      return;
    }
    state.secretsDir = result.data.secrets_dir;
    show("database");
  }).catch(function () {
    button.disabled = false;
    fail("token-error", "The installer did not answer. Is it still running?");
  });
});

// ---- step 2: database -----------------------------------------------------
function refreshDbMode() {
  var external = byName("dbtype") === "external";
  $("db-external").hidden = !external;
  $("db-bundled-note").hidden = external;
  refreshDbForm();
}

function refreshDbForm() {
  var useDsn = byName("dbform") === "dsn";
  $("db-fields").hidden = useDsn;
  $("db-dsn-box").hidden = !useDsn;
  $("plaintext-wrap").hidden = !(!useDsn && $("db-sslmode").value === "disable");
}

function dbPayload() {
  if (byName("dbtype") === "bundled") { return { type: "bundled" }; }
  if (byName("dbform") === "dsn") {
    return {
      type: "external",
      dsn: $("db-dsn").value.trim(),
      password: $("db-dsn-password").value,
      allow_plaintext: $("allow-plaintext").checked
    };
  }
  return {
    type: "external",
    host: $("db-host").value.trim(),
    port: $("db-port").value.trim(),
    dbname: $("db-name").value.trim(),
    user: $("db-user").value.trim(),
    password: $("db-password").value,
    sslmode: $("db-sslmode").value,
    allow_plaintext: $("allow-plaintext").checked
  };
}

function renderChecks(result) {
  var list = $("db-checks");
  list.textContent = "";
  var checks = (result.data && result.data.checks) || [];
  for (var i = 0; i < checks.length; i++) {
    var item = document.createElement("li");
    item.className = checks[i].ok ? "ok" : "bad";
    var mark = document.createElement("span");
    mark.className = "mark";
    mark.textContent = checks[i].ok ? "✓" : "✗";
    var name = document.createElement("span");
    name.className = "name";
    name.textContent = checks[i].name;
    var detail = document.createElement("span");
    detail.className = "detail";
    detail.textContent = checks[i].detail || "";
    item.appendChild(mark); item.appendChild(name); item.appendChild(detail);
    list.appendChild(item);
  }
  var hint = $("db-hint");
  if (result.data && result.data.hint) { hint.textContent = result.data.hint; hint.hidden = false; }
  else { hint.hidden = true; }
}

$("db-validate").addEventListener("click", function () {
  clearError("db-error");
  var button = this;
  button.disabled = true;
  api("/api/validate-db", dbPayload()).then(function (result) {
    button.disabled = false;
    renderChecks(result);
    if (result.status !== 200) { fail("db-error", describeError(result)); return; }
    state.dbValidated = !!result.data.ok;
    if (!result.data.ok) { fail("db-error", "One or more checks failed."); }
  }).catch(function () {
    button.disabled = false;
    fail("db-error", "The installer did not answer.");
  });
});

$("db-next").addEventListener("click", function () {
  clearError("db-error");
  state.db = dbPayload();
  if (state.db.type === "external" && !state.dbValidated) {
    fail("db-error", "Test the connection first — provisioning re-runs the same checks.");
    return;
  }
  show("identity");
});

// ---- step 3: identity -----------------------------------------------------
function refreshIdp() {
  var choice = byName("idptype");
  $("idp-external").hidden = choice !== "external_oidc";
  $("idp-bundled").hidden = choice !== "bundled_keycloak";
}

$("idp-next").addEventListener("click", function () {
  clearError("idp-error");
  var choice = byName("idptype");
  if (choice === "external_oidc") {
    if (!$("oidc-issuer").value.trim() || !$("oidc-client-id").value.trim()) {
      fail("idp-error", "An issuer URL and a client ID are required.");
      return;
    }
    state.idp = {
      type: "external_oidc",
      oidc_issuer: $("oidc-issuer").value.trim(),
      oidc_client_id: $("oidc-client-id").value.trim(),
      oidc_client_secret: $("oidc-client-secret").value
    };
  } else if (choice === "bundled_keycloak") {
    if (!$("bootstrap-email").value.trim()) {
      fail("idp-error", "Your email address is the account you will sign in with.");
      return;
    }
    state.idp = {
      type: "bundled_keycloak",
      kc_admin_user: $("kc-admin-user").value.trim() || "admin",
      bootstrap_admin_email: $("bootstrap-email").value.trim()
    };
  } else {
    state.idp = { type: "none" };
  }
  renderReview();
  show("review");
});

// ---- step 4: review -------------------------------------------------------
function addRow(list, term, detail) {
  var dt = document.createElement("dt");
  dt.textContent = term;
  var dd = document.createElement("dd");
  dd.textContent = detail;
  list.appendChild(dt); list.appendChild(dd);
}

function renderReview() {
  var list = $("review-list");
  list.textContent = "";
  if (state.db.type === "bundled") {
    addRow(list, "Database", "Bundled PostgreSQL, initialised with a generated password.");
  } else {
    addRow(list, "Database", "External PostgreSQL at " + (state.db.host || state.db.dsn || "") +
      " (TLS: " + (state.db.sslmode || "require") + "), validated.");
  }
  if (state.idp.type === "bundled_keycloak") {
    addRow(list, "Sign-in", "Bundled Keycloak. First account: " + state.idp.bootstrap_admin_email);
  } else if (state.idp.type === "external_oidc") {
    addRow(list, "Sign-in", "External provider at " + state.idp.oidc_issuer);
  } else {
    addRow(list, "Sign-in", "Not configured yet.");
  }
  addRow(list, "Secrets directory", state.secretsDir || "(as mounted)");
}

$("provision").addEventListener("click", function () {
  clearError("provision-error");
  var button = this;
  button.disabled = true;
  api("/api/provision", { db: state.db, idp: state.idp }).then(function (result) {
    if (result.status !== 200 || !result.data.ok) {
      button.disabled = false;
      renderChecks(result);
      fail("provision-error", describeError(result));
      return;
    }
    $("done-dir").textContent = result.data.secrets_dir;
    $("done-env").textContent = result.data.env_path;
    var steps = $("next-steps");
    steps.textContent = "";
    var list = result.data.next_steps || [];
    for (var i = 0; i < list.length; i++) {
      var li = document.createElement("li");
      li.textContent = list[i];
      steps.appendChild(li);
    }
    show("done");
  }).catch(function () {
    button.disabled = false;
    fail("provision-error", "The installer did not answer.");
  });
});

// ---- wiring ---------------------------------------------------------------
var dbTypes = document.querySelectorAll('input[name="dbtype"]');
for (var a = 0; a < dbTypes.length; a++) { dbTypes[a].addEventListener("change", refreshDbMode); }
var dbForms = document.querySelectorAll('input[name="dbform"]');
for (var b = 0; b < dbForms.length; b++) { dbForms[b].addEventListener("change", refreshDbForm); }
$("db-sslmode").addEventListener("change", refreshDbForm);
var idpTypes = document.querySelectorAll('input[name="idptype"]');
for (var c = 0; c < idpTypes.length; c++) { idpTypes[c].addEventListener("change", refreshIdp); }

refreshDbMode();
refreshIdp();
show("token");
