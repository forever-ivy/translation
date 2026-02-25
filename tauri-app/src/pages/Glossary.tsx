import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useConfigStore } from "@/stores/configStore";
import { useUiStore } from "@/stores/uiStore";
import * as tauri from "@/lib/tauri";
import {
  BookText,
  Search,
  RefreshCw,
  Plus,
  Save,
  Trash2,
  ChevronLeft,
  ChevronRight,
  FolderOpen,
} from "lucide-react";

export function Glossary() {
  const addToast = useUiStore((s) => s.addToast);
  const config = useConfigStore((s) => s.config);
  const fetchConfig = useConfigStore((s) => s.fetchConfig);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [items, setItems] = useState<tauri.GlossaryTerm[]>([]);
  const [total, setTotal] = useState(0);
  const [companies, setCompanies] = useState<string[]>([]);
  const [languagePairs, setLanguagePairs] = useState<string[]>([]);

  const [query, setQuery] = useState("");
  const [company, setCompany] = useState("");
  const [languagePair, setLanguagePair] = useState("");
  const [page, setPage] = useState(0);
  const pageSize = 50;

  const [formCompany, setFormCompany] = useState("");
  const [formSourceLang, setFormSourceLang] = useState("ar");
  const [formTargetLang, setFormTargetLang] = useState("en");
  const [formSourceText, setFormSourceText] = useState("");
  const [formTargetText, setFormTargetText] = useState("");

  const kbRoot = config?.kbRoot || "";
  const glossaryRoot = kbRoot ? `${kbRoot}/00_Glossary` : "";

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  useEffect(() => {
    let cancelled = false;
    const handle = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await tauri.listGlossaryTerms({
          company: company || undefined,
          languagePair: languagePair || undefined,
          query: query.trim() || undefined,
          limit: pageSize,
          offset: page * pageSize,
        });
        if (cancelled) return;
        setItems(res.items || []);
        setTotal(res.total || 0);
        setCompanies(res.companies || []);
        setLanguagePairs(res.language_pairs || []);
        if (!formCompany && (res.companies || []).length > 0) {
          setFormCompany(res.companies[0]);
        }
      } catch (err) {
        if (cancelled) return;
        addToast("error", `Failed to load glossary: ${String(err)}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 150);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [addToast, company, languagePair, page, query]);

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / pageSize)), [total]);

  const resetForm = () => {
    setFormSourceLang("ar");
    setFormTargetLang("en");
    setFormSourceText("");
    setFormTargetText("");
  };

  const reload = async () => {
    try {
      setLoading(true);
      const res = await tauri.listGlossaryTerms({
        company: company || undefined,
        languagePair: languagePair || undefined,
        query: query.trim() || undefined,
        limit: pageSize,
        offset: page * pageSize,
      });
      setItems(res.items || []);
      setTotal(res.total || 0);
      setCompanies(res.companies || []);
      setLanguagePairs(res.language_pairs || []);
    } catch (err) {
      addToast("error", `Failed to reload glossary: ${String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  const onSave = async () => {
    const c = (formCompany || company).trim();
    if (!c || !formSourceText.trim() || !formTargetText.trim()) {
      addToast("warning", "Company, source text and target text are required");
      return;
    }
    setSaving(true);
    try {
      await tauri.upsertGlossaryTerm({
        company: c,
        sourceLang: formSourceLang.trim() || "ar",
        targetLang: formTargetLang.trim() || "en",
        sourceText: formSourceText,
        targetText: formTargetText,
      });
      addToast("success", "Glossary term saved");
      resetForm();
      setPage(0);
      await reload();
    } catch (err) {
      addToast("error", `Failed to save glossary term: ${String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const onEdit = (item: tauri.GlossaryTerm) => {
    setFormCompany(item.company);
    setFormSourceLang(item.source_lang);
    setFormTargetLang(item.target_lang);
    setFormSourceText(item.source_text);
    setFormTargetText(item.target_text);
  };

  const onDelete = async (item: tauri.GlossaryTerm) => {
    const ok = window.confirm(`Delete term?\n${item.source_text}\n→ ${item.target_text}`);
    if (!ok) return;
    setSaving(true);
    try {
      await tauri.deleteGlossaryTerm({
        company: item.company,
        sourceLang: item.source_lang,
        targetLang: item.target_lang,
        sourceText: item.source_text,
      });
      addToast("success", "Glossary term deleted");
      await reload();
    } catch (err) {
      addToast("error", `Failed to delete glossary term: ${String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Glossary</h2>
          <p className="text-muted-foreground">Browse and manage extracted glossary terms by language pair</p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={async () => {
              if (glossaryRoot) await tauri.openInFinder(glossaryRoot);
            }}
            disabled={!glossaryRoot}
          >
            <FolderOpen className="h-4 w-4 mr-2" />
            Open Glossary Root
          </Button>
          <Button variant="outline" onClick={reload} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Plus className="h-4 w-4" />
            Add / Edit Term
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
            <input
              value={formCompany}
              onChange={(e) => setFormCompany(e.target.value)}
              placeholder="Company (e.g. Eventranz)"
              className="px-3 py-2 border rounded-xl text-sm bg-background"
              list="glossary-companies"
            />
            <datalist id="glossary-companies">
              {companies.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>

            <input
              value={formSourceLang}
              onChange={(e) => setFormSourceLang(e.target.value)}
              placeholder="Source lang (ar)"
              className="px-3 py-2 border rounded-xl text-sm bg-background"
            />
            <input
              value={formTargetLang}
              onChange={(e) => setFormTargetLang(e.target.value)}
              placeholder="Target lang (en)"
              className="px-3 py-2 border rounded-xl text-sm bg-background"
            />
            <Button variant="outline" onClick={onSave} disabled={saving}>
              <Save className="h-4 w-4 mr-2" />
              Save
            </Button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <textarea
              value={formSourceText}
              onChange={(e) => setFormSourceText(e.target.value)}
              placeholder="Source term/text"
              rows={3}
              className="px-3 py-2 border rounded-xl text-sm bg-background resize-y"
            />
            <textarea
              value={formTargetText}
              onChange={(e) => setFormTargetText(e.target.value)}
              placeholder="Target term/text"
              rows={3}
              className="px-3 py-2 border rounded-xl text-sm bg-background resize-y"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <BookText className="h-4 w-4" />
            Terms
            <Badge variant="outline">{total} total</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2 flex-wrap items-center">
            <div className="relative flex-1 min-w-[240px]">
              <Search className="h-4 w-4 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setPage(0);
                }}
                placeholder="Search terms..."
                className="w-full pl-9 pr-3 py-2 border rounded-xl text-sm bg-background"
              />
            </div>

            <select
              value={company}
              onChange={(e) => {
                setCompany(e.target.value);
                setPage(0);
              }}
              className="px-3 py-2 border rounded-xl text-sm bg-background"
            >
              <option value="">All companies</option>
              {companies.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>

            <select
              value={languagePair}
              onChange={(e) => {
                setLanguagePair(e.target.value);
                setPage(0);
              }}
              className="px-3 py-2 border rounded-xl text-sm bg-background"
            >
              <option value="">All pairs</option>
              {languagePairs.map((lp) => (
                <option key={lp} value={lp}>
                  {lp}
                </option>
              ))}
            </select>

            <div className="flex items-center gap-2">
              <Button variant="ghost" size="icon" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page <= 0}>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="text-xs text-muted-foreground">
                Page {page + 1} / {totalPages}
              </span>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setPage((p) => (p + 1 < totalPages ? p + 1 : p))}
                disabled={page + 1 >= totalPages}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>

          <div className="rounded-xl border overflow-x-auto">
            <div className="min-w-[1000px]">
              <div className="grid grid-cols-12 bg-muted/40 text-xs text-muted-foreground">
                <div className="col-span-2 p-3">Company</div>
                <div className="col-span-1 p-3">Pair</div>
                <div className="col-span-3 p-3">Source</div>
                <div className="col-span-3 p-3">Target</div>
                <div className="col-span-1 p-3">Origin</div>
                <div className="col-span-2 p-3 text-right">Actions</div>
              </div>
              {loading ? (
                <div className="p-4 text-sm text-muted-foreground">Loading…</div>
              ) : items.length === 0 ? (
                <div className="p-4 text-sm text-muted-foreground">No terms found.</div>
              ) : (
                <div className="divide-y">
                  {items.map((item, idx) => (
                    <div key={`${item.company}-${item.language_pair}-${item.source_text}-${idx}`} className="grid grid-cols-12 text-sm hover:bg-muted/20">
                      <div className="col-span-2 p-3">{item.company}</div>
                      <div className="col-span-1 p-3">
                        <Badge variant="secondary" className="text-xs">
                          {item.language_pair}
                        </Badge>
                      </div>
                      <div className="col-span-3 p-3 whitespace-pre-wrap break-words">{item.source_text}</div>
                      <div className="col-span-3 p-3 whitespace-pre-wrap break-words">{item.target_text}</div>
                      <div className="col-span-1 p-3">
                        <Badge variant={item.origin === "custom" ? "default" : "outline"} className="text-xs">
                          {item.origin}
                        </Badge>
                      </div>
                      <div className="col-span-2 p-3 flex justify-end gap-1">
                        <Button variant="ghost" size="icon" onClick={() => onEdit(item)} title="Edit into form">
                          <Save className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="icon" onClick={() => onDelete(item)} title="Delete term">
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
