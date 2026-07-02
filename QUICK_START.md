# ⚡ QUICK START: Deploy in 10 Minutes

## Your Application is Ready! 🎉

All files are prepared and ready to deploy. Follow these simple steps:

---

## STEP 1: Download All Files (2 min)

All files are in your outputs folder:
```
app.py
requirements.txt
Procfile
runtime.txt
README.md
DEPLOYMENT_GUIDE.md
templates/index.html
.gitignore
```

**Download them all to your computer.**

---

## STEP 2: Create GitHub Repository (2 min)

1. Go to: https://github.com/new
2. Repository name: `title-automation-tool`
3. Description: `Listenfirst Title Data Automation Tool`
4. Public
5. **Click "Create repository"**

---

## STEP 3: Push Code to GitHub (3 min)

Open terminal/command prompt in the folder with your files:

```bash
git init
git add .
git commit -m "Initial commit: Title Automation Tool"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/title-automation-tool.git
git push -u origin main
```

**Replace `YOUR_USERNAME` with your actual GitHub username**

---

## STEP 4: Deploy to Render (3 min)

1. Go to: https://render.com
2. Sign in (or create free account)
3. Click **"New +"** → **"Web Service"**

### Connect Repository:
- Click **"Connect account"** (GitHub)
- Select: `title-automation-tool`
- Branch: `main`
- Click **"Connect"**

### Configure Service:
```
Name:                    title-automation-tool
Environment:             Python 3
Build Command:           pip install -r requirements.txt
Start Command:           gunicorn app:app
Instance Type:           Free
```

5. Click **"Create Web Service"**

---

## STEP 5: Wait for Deployment (3-5 min)

- Render will automatically deploy
- You'll see a live URL: `https://title-automation-tool.onrender.com`
- Status will change from "Deploying" to "Live"

---

## ✨ YOUR APP IS LIVE!

Once deployed:
- Visit your live URL
- Paste movie/TV show titles
- Click "Preview"
- Click "Download Excel"
- Done! 🚀

---

## Video Tutorial (Alternative)

If you prefer visual guidance:
1. Create repo: https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-new-repository
2. Deploy to Render: https://docs.render.com/deploy-from-github

---

## Troubleshooting

### Git push fails?
```bash
# Make sure you're in the right folder
# And you've created the repo on GitHub first
```

### Render deployment fails?
- Check the build logs in Render dashboard
- Verify requirements.txt exists
- Check Python version compatibility

### App doesn't work?
- Wait a few minutes after deployment
- Hard refresh your browser (Ctrl+Shift+R)
- Check Render logs for errors

---

## Questions?

Check:
- `README.md` - Full documentation
- `DEPLOYMENT_GUIDE.md` - Detailed guide
- Render Dashboard - Live logs

---

**You've got this! Deploy now and let me know if you need help! 🎯**
