# Deploy in 6 steps (short)

**Already done for you:** Docker image is built. Fly CLI is installed. Project is ready to deploy.

---

## Step 0: Use the Fly command (run this once per Terminal window)

If `fly` isn’t found, run this so your shell can find it:

```bash
export PATH="$HOME/.fly/bin:$PATH"
```

Check: `fly version` should show a version number.

---

## Step 1: Go to the project and log in to Fly

```bash
cd /Users/lincksmorara/Desktop/rentalManagement/rent-reconciliation
fly auth login
```

A browser opens → sign up or log in with email/GitHub → when it says “Success”, you’re done.

---

## Step 2: Create the app and disk (one time only)

Run these **one after the other**:

```bash
fly apps create rent-reconciliation
```

If it says the name is taken, use a different name (e.g. `rent-recon-yourname`) and remember it for when you run `fly deploy`.

```bash
fly volumes create data_vol --region jnb --size 1
```

---

## Step 3: Set your passwords

Replace `YourAdminPass123` and `YourViewerPass123` with real passwords you’ll remember:

```bash
fly secrets set SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" ADMIN_PASSWORD="YourAdminPass123" VIEWER_PASSWORD="YourViewerPass123"
```

---

## Step 4: Deploy

```bash
fly deploy
```

Wait until it finishes. It will print a URL like `https://rent-reconciliation.fly.dev`.

---

## Step 5: Open the app

```bash
fly open
```

Log in with the **admin** password you set in Step 4. You can now add your first property (Onboard or Setup).

---

## Later: update the app after code changes

From the project folder:

```bash
cd /Users/lincksmorara/Desktop/rentalManagement/rent-reconciliation
fly deploy
```

That’s it.
