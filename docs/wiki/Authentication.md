# Authentication

HTTP Basic Auth on every route except `/healthz`. There is **no factory password**: a static
default shipped in an image is a public credential by definition, and the first person to read
the README knows it.

---

## Two modes, and which one you are in

### Environment mode — `DASHBOARD_PASSWORD` is set

The environment is the source of truth. The password is whatever the variable says, on every
start.

Changing it on screen is **refused**, with an explicit notice: *credentials defined by
environment variable; change them in the environment and restart*. Accepting the change would be
worse than refusing it — the panel would appear to obey and then revert at the next recreate,
leaving the operator convinced of a password that does not work.

To change it: edit the environment and recreate the container.

```bash
docker compose up -d --force-recreate litellmrtksync
```

### Stored mode — `DASHBOARD_PASSWORD` is empty

The password lives in the panel's own storage, inside `DATA_DIR`, and the screen is where you
change it. This is the mode that lets each deployment have its own password without editing
infrastructure.

---

## First access: the recovery credential

On first boot in stored mode, nobody has set a password yet. The panel generates a **recovery
credential** and writes it to `.dashboard_recovery` inside the data directory, with mode `0600`.

The log records **the file, never the value**:

```
[AUTH] Recovery credential generated at /app/data/.dashboard_recovery (mode 0600)
```

That distinction is the whole point. Log lines get shipped to aggregators, pasted into tickets
and read by people who should not have the credential.

Read it, sign in as `admin` with it, and set your own password on the screen.

```bash
docker exec litellmrtksync cat /app/data/.dashboard_recovery
```

### It keeps working after you set a password

The recovery credential is **break-glass**: it stays valid after a normal password is set. That
is deliberate — a break-glass credential that expires when you set a password is useless exactly
when you need it, which is when you have forgotten the password.

Keep the data directory as private as any other secret store. Anyone who can read that file can
enter the panel.

### Providing your own

`DASHBOARD_RECOVERY_HASH` accepts a pre-computed hash, so the credential never has to be
generated on the machine. Useful when the data directory is shared or rebuilt often.

---

## Password policy

A new password must be at least **6 characters**. A shorter one is refused and the previous
password keeps working — the change simply does not happen, rather than leaving the panel in a
state nobody can enter.

Passwords are stored hashed (PBKDF2-SHA256), never in clear text.

---

## What is never exposed

- The **master key** of the proxy: it is read from the environment and used to call the admin
  API. It never appears in a page, a log line or a JSON response.
- **Virtual keys**: shown by alias when there is one, masked to the last six characters when
  there is not.
- The **panel password**: not in the `401` body, not in a banner, not in `--help`.

The `401` body deserves its own mention: it is what the browser renders when you press **ESC** on
the Basic Auth dialog, so it is a page users actually see. It carries the same security headers
as every other response, and says nothing about which credentials exist.

---

## Cross-origin requests

A `POST` arriving from another origin is refused. The browser attaches Basic Auth credentials to
a third-party form on its own, so without this check a page on another site could trigger an
action on your panel simply because you were signed in.
