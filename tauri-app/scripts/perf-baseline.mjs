import fs from "fs";
import path from "path";

const distDir = path.resolve(process.cwd(), "dist/assets");
if (!fs.existsSync(distDir)) {
  console.error("dist/assets not found. Run npm run build first.");
  process.exit(1);
}

const files = fs.readdirSync(distDir).filter((name) => name.endsWith(".js"));
const entries = files.map((name) => {
  const full = path.join(distDir, name);
  const stat = fs.statSync(full);
  return { name, bytes: stat.size };
});
entries.sort((a, b) => b.bytes - a.bytes);

const maxBundleBytes = 550_000;
const largest = entries[0];

console.log("Largest JS assets:");
for (const item of entries.slice(0, 5)) {
  console.log(`- ${item.name}: ${item.bytes} bytes`);
}

if (largest && largest.bytes > maxBundleBytes) {
  console.error(`Performance baseline failed: ${largest.name} is ${largest.bytes} bytes (max ${maxBundleBytes})`);
  process.exit(2);
}

console.log("Performance baseline passed.");
