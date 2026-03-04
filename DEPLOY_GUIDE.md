# Rent Reconciliation – Deploy guide for beginners

This guide assumes you’ve never used Docker or Fly.io. It explains the ideas in plain language and gives step‑by‑step commands for your project.

---

## What you’re doing in two sentences

- **Docker** packages your app and its dependencies so it runs the same way on your laptop and on a server.
- **Fly.io** is a hosting service: you send that package to them, and they give you a public URL (e.g. `https://rent-reconciliation.fly.dev`) so you can use the app from anywhere.

---

## Part 1: Install the tools (one time)

### 1.1 Install Docker Desktop

Docker runs your app in a “container” (a small, isolated environment) on your Mac.

1. Go to **https://www.docker.com/products/docker-desktop/**
2. Download **Docker Desktop for Mac** (Apple Silicon or Intel, depending on your Mac).
3. Open the downloaded file and drag Docker into Applications.
4. Open **Docker** from Applications. Wait until the whale icon in the menu bar says Docker is running (no “Starting…”).
5. Check it works: open **Terminal** and run:
   ```bash
   docker --version
   ```
   You should see something like `Docker version 24.x.x`. If you get “command not found”, Docker isn’t in your PATH yet; restart Terminal and try again.

### 1.2 Install Fly.io’s CLI (flyctl)

The Fly.io CLI is the tool you use to create and update your app on Fly.io.

1. Open **Terminal**.
2. Run this (it downloads and installs the `flyctl` command):
   ```bash
   curl -L https://fly.io/install.sh | sh
   ```
3. The installer may say “Add this to your PATH”. If it does, run the command it prints (often something like):
   ```bash
   export FLYCTL_INSTALL="/Users/yourname/.fly"
   export PATH="$FLYCTL_INSTALL/bin:$PATH"
   ```
   To make that permanent, add those two lines to your shell config (e.g. `~/.zshrc`) and then run `source ~/.zshrc` or open a new terminal.
4. Check it works:
   ```bash
   fly version
   ```
   You should see a version number.

---

## Part 2: Run the app with Docker on your Mac (optional)

This part is only to get comfortable with Docker. You don’t need it for Fly.io, but it’s a good sanity check.

### 2.1 Open the project in Terminal

```bash
cd /Users/lincksmorara/Desktop/rentalManagement/rent-reconciliation
```

### 2.2 Build the Docker image

This reads your `Dockerfile` and builds an image named `rent-reconciliation`:

```bash
docker build -t rent-reconciliation .
```

- The first time it takes a few minutes (downloading Python and libraries).
- When it finishes, you should see “writing image” and “naming to ... rent-reconciliation” with no errors.

### 2.3 Create a folder for “production” data (so the app can save the database)

Your app stores the database in `data/`. In the container we’ll map a folder to `/data` so that data isn’t lost when the container stops.

```bash
mkdir -p ./data-fly
```

You only need this folder when running with Docker; your normal `data/` folder is for local development.

### 2.4 Run the container

```bash
docker run --rm -p 8080:8080 \
  -e DATABASE_PATH=/data/rent.db \
  -e UPLOAD_FOLDER=/data/statements \
  -v "$(pwd)/data-fly:/data" \
  rent-reconciliation
```

What this does:

- `--rm` – delete the container when you stop it.
- `-p 8080:8080` – your Mac’s port 8080 is the app’s port 8080.
- `-e DATABASE_PATH=...` – tell the app to use the database at `/data/rent.db` inside the container.
- `-e UPLOAD_FOLDER=...` – tell the app to store uploaded files in `/data/statements`.
- `-v "$(pwd)/data-fly:/data"` – the folder `data-fly` on your Mac is seen as `/data` in the container (so the DB and uploads persist).
- `rent-reconciliation` – the image name you built.

Leave this terminal open. You should see “Booting worker” and no Python errors.

### 2.5 Open the app in the browser

- Go to: **http://127.0.0.1:8080**
- If you have admin auth on (e.g. `ADMIN_PASSWORD` set), you’ll be sent to the login page; otherwise you’ll see the property list or dashboard.

To stop the app: in that terminal press **Ctrl+C**. The container will be removed; your database stays in `data-fly/`.

---

## Part 3: Put the app on the internet with Fly.io

Here you create an app on Fly.io, attach a disk so the database persists, set secrets, and deploy.

### 3.1 Log in to Fly.io

In Terminal:

```bash
fly auth login
```

- A browser window opens.
- Sign up (email or GitHub) or log in.
- When it says “Success”, you can close the browser and go back to Terminal.

### 3.2 Go to your project folder

```bash
cd /Users/lincksmorara/Desktop/rentalManagement/rent-reconciliation
```

### 3.3 Create the Fly app (once per project)

```bash
fly apps create rent-reconciliation
```

- If the name `rent-reconciliation` is taken (by someone else), pick another, e.g. `rent-reconciliation-yourname`, and use that name everywhere below instead of `rent-reconciliation`.

### 3.4 Create a volume (disk) for the database and uploads

Your app needs a persistent disk so the SQLite database and uploaded files aren’t lost on each deploy.

```bash
fly volumes create data_vol --region jnb --size 1
```

- `jnb` = Johannesburg (good for Kenya).
- `1` = 1 GB. You can increase later if needed.
- When it succeeds, you’ll see a volume id. You only do this once; `fly.toml` already says to use `data_vol`.

### 3.5 Set secrets (passwords and key)

Never put real passwords in code or in `fly.toml`. Use **secrets** so they’re stored securely.

**Generate a random secret key and set all three at once:**

```bash
fly secrets set SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" \
  ADMIN_PASSWORD="choose-a-strong-admin-password" \
  VIEWER_PASSWORD="choose-a-viewer-password"
```

- Replace `choose-a-strong-admin-password` and `choose-a-viewer-password` with real passwords you’ll remember.
- After this, Fly.io will use these values when running your app; you don’t see them in the dashboard.

### 3.6 Deploy

From the same folder (where `fly.toml` and `Dockerfile` are):

```bash
fly deploy
```

- Fly builds the image (like `docker build`) and uploads it, then starts the app and attaches the volume.
- First deploy can take a few minutes. When it finishes you’ll see a line like “Visit your newly deployed app at https://rent-reconciliation.fly.dev”.

### 3.7 Open your live app

Either click the link Fly printed, or run:

```bash
fly open
```

- You should see the **login page** (because `ADMIN_PASSWORD` is set).
- Log in with the admin password you set in step 3.5.
- Then you can create your first property via **Onboard Property** or **Setup**, and use the app as normal.

---

## Part 4: After the first deploy

### 4.1 Changing code and redeploying

Whenever you change the app (Python, templates, etc.):

```bash
cd /Users/lincksmorara/Desktop/rentalManagement/rent-reconciliation
fly deploy
```

- The database and uploads on the volume are kept; only the app code is updated.

### 4.2 Changing passwords or SECRET_KEY

```bash
fly secrets set ADMIN_PASSWORD="new-password"
# or set several at once:
fly secrets set SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" ADMIN_PASSWORD="new-admin" VIEWER_PASSWORD="new-viewer"
```

- After changing secrets, Fly restarts the app automatically.

### 4.3 Copying your local database to Fly (optional)

If you already have data in `data/rent.db` on your Mac and want that on Fly:

1. Deploy once (so the app and volume exist).
2. From your project folder:
   ```bash
   fly ssh sftp shell
   ```
3. In the SFTP shell:
   ```bash
   put data/rent.db /data/rent.db
   bye
   ```
   (Use the path to your real `rent.db` if it’s different.)
4. Restart the app so it picks up the file:
   ```bash
   fly apps restart rent-reconciliation
   ```

### 4.4 Viewing logs

To see what the app is doing (errors, requests):

```bash
fly logs
```

Leave it open to stream logs; Ctrl+C to stop.

### 4.5 Stopping or destroying the app

- **Stop** (no cost for the app, you still pay a little for the volume until you delete it):
  ```bash
  fly scale count 0
  ```
- **Start again:**
  ```bash
  fly scale count 1
  ```
- **Delete app and volume** (permanent; deletes DB and uploads):
  ```bash
  fly apps destroy rent-reconciliation
  ```
  Confirm when asked.

---

## Quick reference

| Goal                    | Command |
|-------------------------|--------|
| Build Docker image      | `docker build -t rent-reconciliation .` |
| Run locally with Docker | `docker run --rm -p 8080:8080 -e DATABASE_PATH=/data/rent.db -e UPLOAD_FOLDER=/data/statements -v "$(pwd)/data-fly:/data" rent-reconciliation` |
| Log in to Fly           | `fly auth login` |
| Deploy                  | `fly deploy` |
| Open live app           | `fly open` |
| Set secrets             | `fly secrets set ADMIN_PASSWORD="..." VIEWER_PASSWORD="..." SECRET_KEY="..."` |
| View logs               | `fly logs` |

---

## If something goes wrong

- **“fly: command not found”**  
  Install flyctl (Part 1.2) and add it to your PATH (the line the installer prints).

- **“No space left on device” or build fails on Fly**  
  The free tier has limits. Try `fly scale count 0`, then `fly deploy` again, or reduce the size of what you’re copying (e.g. don’t put huge files in the app folder).

- **App starts but “502 Bad Gateway” or blank page**  
  Check that the app listens on port **8080** (your Dockerfile and fly.toml already do). Run `fly logs` and fix any Python errors it shows.

- **Can’t log in / “Invalid password”**  
  You must set `ADMIN_PASSWORD` with `fly secrets set`. After changing secrets, wait a few seconds and try again.

- **Database or uploads disappear after deploy**  
  Make sure you created a volume (step 3.4) and that `fly.toml` has the `[mounts]` section (it does). Then redeploy so the app uses that volume.

If you tell me the exact error message or the step you’re on, I can give you a minimal fix for that step.
