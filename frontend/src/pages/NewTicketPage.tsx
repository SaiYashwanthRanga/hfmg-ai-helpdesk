import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ArrowLeft, Plus } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { toast } from "../lib/toastStore";
import type { Category, Priority, TicketCreateInput } from "../types/ticket";
import { PRIORITY_OPTIONS } from "../types/ticket";

const emptyForm: TicketCreateInput = {
  caller_name: "",
  phone_number: "",
  email: "",
  category_id: "",
  priority: undefined,
  description: "",
};

export function NewTicketPage() {
  const navigate = useNavigate();
  const [categories, setCategories] = useState<Category[]>([]);
  const [form, setForm] = useState<TicketCreateInput>(emptyForm);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .listCategories()
      .then((cats) => {
        setCategories(cats);
        if (cats.length > 0) {
          setForm((f) => ({ ...f, category_id: cats[0].id }));
        }
      })
      .catch(() => setSubmitError("Failed to load categories from database"));
  }, []);

  function validate(): boolean {
    const errors: Record<string, string> = {};
    if (!form.caller_name.trim()) errors.caller_name = "Caller name is required";
    if (!form.phone_number.trim()) errors.phone_number = "Phone number is required";
    if (!form.category_id) errors.category_id = "Category is required";
    if (!form.description.trim()) errors.description = "Description is required";
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    if (!validate()) return;

    setSubmitting(true);
    try {
      const payload: TicketCreateInput = {
        ...form,
        email: form.email?.trim() ? form.email.trim() : undefined,
      };
      const ticket = await api.createTicket(payload);
      toast.success(`Ticket ${ticket.ticket_number} created successfully`);
      navigate(`/tickets?ticket=${ticket.id}`);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : "Failed to create ticket");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <Link
          to="/tickets"
          className="inline-flex size-8 items-center justify-center rounded-lg border border-stone-200/80 bg-white text-stone-600 shadow-2xs hover:bg-stone-50 hover:text-stone-900 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-800"
          title="Back to Tickets"
          aria-label="Back to Tickets"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-stone-900">Create New Ticket</h1>
          <p className="text-xs text-stone-500">Record a new manual incident or service request into PostgreSQL.</p>
        </div>
      </div>

      <Card className="p-6">
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <Field label="Caller Name" error={fieldErrors.caller_name}>
            <input
              type="text"
              value={form.caller_name}
              onChange={(e) => setForm({ ...form, caller_name: e.target.value })}
              placeholder="e.g. Maria Gomez"
              className="w-full rounded-lg border border-stone-200/80 bg-stone-50/50 px-3 py-2 text-sm text-stone-900 placeholder:text-stone-400 focus:border-emerald-800 focus:bg-white focus:outline-none focus:ring-1 focus:ring-emerald-800"
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Phone Number" error={fieldErrors.phone_number}>
              <input
                type="tel"
                value={form.phone_number}
                onChange={(e) => setForm({ ...form, phone_number: e.target.value })}
                placeholder="+15551234567"
                className="w-full rounded-lg border border-stone-200/80 bg-stone-50/50 px-3 py-2 text-sm text-stone-900 placeholder:text-stone-400 focus:border-emerald-800 focus:bg-white focus:outline-none focus:ring-1 focus:ring-emerald-800"
              />
            </Field>

            <Field label="Email (Optional)">
              <input
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                placeholder="caller@example.com"
                className="w-full rounded-lg border border-stone-200/80 bg-stone-50/50 px-3 py-2 text-sm text-stone-900 placeholder:text-stone-400 focus:border-emerald-800 focus:bg-white focus:outline-none focus:ring-1 focus:ring-emerald-800"
              />
            </Field>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Category" error={fieldErrors.category_id}>
              <select
                value={form.category_id}
                onChange={(e) => setForm({ ...form, category_id: e.target.value })}
                className="w-full rounded-lg border border-stone-200/80 bg-stone-50/50 px-3 py-2 text-sm text-stone-900 focus:border-emerald-800 focus:bg-white focus:outline-none focus:ring-1 focus:ring-emerald-800"
              >
                {categories.map((cat) => (
                  <option key={cat.id} value={cat.id}>
                    {cat.name}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Priority (Optional — defaults from category)">
              <select
                value={form.priority ?? ""}
                onChange={(e) =>
                  setForm({ ...form, priority: (e.target.value || undefined) as Priority | undefined })
                }
                className="w-full rounded-lg border border-stone-200/80 bg-stone-50/50 px-3 py-2 text-sm text-stone-900 focus:border-emerald-800 focus:bg-white focus:outline-none focus:ring-1 focus:ring-emerald-800"
              >
                <option value="">Default (From category)</option>
                {PRIORITY_OPTIONS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <Field label="Description" error={fieldErrors.description}>
            <textarea
              rows={5}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="Describe the issue, symptoms, and troubleshooting steps taken..."
              className="w-full rounded-lg border border-stone-200/80 bg-stone-50/50 px-3 py-2 text-sm text-stone-900 placeholder:text-stone-400 focus:border-emerald-800 focus:bg-white focus:outline-none focus:ring-1 focus:ring-emerald-800 resize-y"
            />
          </Field>

          {submitError && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700">
              {submitError}
            </div>
          )}

          <div className="mt-2 flex items-center justify-end gap-3 pt-4 border-t border-stone-100">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => navigate("/tickets")}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              loading={submitting}
              icon={<Plus className="size-3.5" />}
            >
              Submit Ticket
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-semibold text-stone-800">{label}</label>
      {children}
      {error && <span className="text-[11px] font-medium text-rose-600">{error}</span>}
    </div>
  );
}
