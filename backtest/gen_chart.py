import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import json, os

with open("/Users/andreas/bitget_bot_v1/backtest/compare_10_variants.json") as f:
    data = json.load(f)

ranking = data["ranking"]

names = [r["name"] for r in ranking]
pnls = [r["pnl"] for r in ranking]
wrs = [r["wr"] for r in ranking]
pfs = [r["pf"] for r in ranking]
max_dds = [r["max_dd"] for r in ranking]
trades = [r["trades"] for r in ranking]

colors = ["#22c55e" if p >= 0 else "#ef4444" for p in pnls]

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [1.5, 1]})
fig.patch.set_facecolor("#1a1a2e")

# === TOP: PnL horizontal bars ===
bars = ax1.barh(range(len(names)), pnls, color=colors, alpha=0.85, edgecolor="white", linewidth=0.5, height=0.65)
ax1.set_yticks(range(len(names)))
ax1.set_yticklabels(names, fontsize=10, color="#e0e0e0")
ax1.axvline(0, color="#555", linewidth=1)
ax1.set_xlabel("PnL (USDT)", fontsize=11, color="#aaa")
ax1.set_title("Backtest-Vergleich: 10 Varianten — PnL (1 Jahr)", fontsize=14, color="white", fontweight="bold", pad=15)
ax1.set_facecolor("#16213e")
ax1.tick_params(colors="#aaa")

for bar, pnl, wr, pf, dd in zip(bars, pnls, wrs, pfs, max_dds):
    x = bar.get_width()
    y = bar.get_y() + bar.get_height()/2
    label = f" {pnl:+.0f} USDT | WR {wr:.1f}% | PF {pf:.2f} | DD {dd:.0f}$"
    if x >= 0:
        ax1.text(x + 5, y, label, va="center", fontsize=8, color="#ccc")
    else:
        ax1.text(x - 5, y, label, va="center", ha="right", fontsize=8, color="#ccc")

# Highlight winner
bars[0].set_edgecolor("#fbbf24")
bars[0].set_linewidth(3)

green_patch = mpatches.Patch(color="#22c55e", label="Positiv")
red_patch = mpatches.Patch(color="#ef4444", label="Negativ")
gold_patch = mpatches.Patch(color="#fbbf24", label="Sieger (Variante 10)")
ax1.legend(handles=[green_patch, red_patch, gold_patch], loc="lower right", fontsize=9, facecolor="#1a1a2e", edgecolor="#333", labelcolor="#ccc")

ax1.set_xlim(min(pnls) - 150, max(pnls) + 200)
ax1.grid(axis="x", alpha=0.15, color="#555")
ax1.spines["top"].set_visible(False)
ax1.spines["right"].set_visible(False)
ax1.spines["left"].set_color("#333")
ax1.spines["bottom"].set_color("#333")

# === BOTTOM: WR, PF×100, DD grouped ===
x = list(range(len(names)))
w = 0.25

ax2.bar([i - w for i in x], wrs, width=w, color="#3b82f6", alpha=0.85, label="Win Rate (%)", edgecolor="white", linewidth=0.3)
ax2.bar(x, [p * 100 for p in pfs], width=w, color="#a855f7", alpha=0.85, label="Profit Factor ×100", edgecolor="white", linewidth=0.3)
ax2.bar([i + w for i in x], max_dds, width=w, color="#f97316", alpha=0.85, label="Max Drawdown ($)", edgecolor="white", linewidth=0.3)

ax2.set_xticks(x)
ax2.set_xticklabels(names, fontsize=8, color="#e0e0e0", rotation=25, ha="right")
ax2.set_ylabel("Wert", fontsize=11, color="#aaa")
ax2.set_title("Win Rate, Profit Factor & Max Drawdown pro Variante", fontsize=12, color="white", fontweight="bold", pad=10)
ax2.set_facecolor("#16213e")
ax2.tick_params(colors="#aaa")
ax2.legend(fontsize=8, facecolor="#1a1a2e", edgecolor="#333", labelcolor="#ccc")
ax2.grid(axis="y", alpha=0.15, color="#555")
ax2.spines["top"].set_visible(False)
ax2.spines["right"].set_visible(False)
ax2.spines["left"].set_color("#333")
ax2.spines["bottom"].set_color("#333")

plt.tight_layout(pad=2)
os.makedirs("/Users/andreas/bitget_bot_v1/backtest", exist_ok=True)
plt.savefig("/Users/andreas/bitget_bot_v1/backtest/varianten_vergleich.png", dpi=150, bbox_inches="tight", facecolor="#1a1a2e")
print("✅ Chart saved!")
