// CareLoop authorization policies (Cedar subset — see careloop/authz.cedar_decide).
// Entity model:
//   principal  = { role: "caregiver"|"elder"|"viewer", role_group: "HouseholdMembers", hid }
//   action     = one of ViewPlan ViewDose MarkDone ConfirmItem AddNote DeleteItem
//                ExportPlan ManagePeople
//   resource   = { hid }
//   context    = { surface: "alexa"|"companion" }
// Default is deny; every rule is scoped to the principal's own household.

// ---------- caregiver ----------
permit(principal == Principal::"caregiver", action in Action::"ViewPlan", resource);
permit(principal == Principal::"caregiver", action in Action::"ViewDose", resource) when { context.surface == "companion" };
permit(principal == Principal::"caregiver", action in Action::"MarkDone", resource);
permit(principal == Principal::"caregiver", action in Action::"ConfirmItem", resource);
permit(principal == Principal::"caregiver", action in Action::"AddNote", resource);
permit(principal == Principal::"caregiver", action in Action::"DeleteItem", resource);
permit(principal == Principal::"caregiver", action in Action::"ExportPlan", resource) when { context.surface == "companion" };
permit(principal == Principal::"caregiver", action in Action::"ManagePeople", resource);

// ---------- elder ----------
permit(principal == Principal::"elder", action in Action::"ViewPlan", resource);
permit(principal == Principal::"elder", action in Action::"MarkDone", resource);
permit(principal == Principal::"elder", action in Action::"ConfirmItem", resource);
permit(principal == Principal::"elder", action in Action::"AddNote", resource);

// ---------- viewer ----------
permit(principal == Principal::"viewer", action in Action::"ViewPlan", resource);

// ---------- hard forbid on the Alexa surface (the dose-free rule as policy) ----------
forbid(principal in Principal::"HouseholdMembers", action in Action::"ViewDose", resource) when { context.surface == "alexa" };
forbid(principal in Principal::"HouseholdMembers", action in Action::"ExportPlan", resource) when { context.surface == "alexa" };

// ---------- cross-household is never allowed ----------
forbid(principal in Principal::"HouseholdMembers", action in Action::"ViewPlan", resource) when { principal.hid != resource.hid };
forbid(principal in Principal::"HouseholdMembers", action in Action::"ViewDose", resource) when { principal.hid != resource.hid };
forbid(principal in Principal::"HouseholdMembers", action in Action::"MarkDone", resource) when { principal.hid != resource.hid };
forbid(principal in Principal::"HouseholdMembers", action in Action::"ConfirmItem", resource) when { principal.hid != resource.hid };
forbid(principal in Principal::"HouseholdMembers", action in Action::"AddNote", resource) when { principal.hid != resource.hid };
forbid(principal in Principal::"HouseholdMembers", action in Action::"DeleteItem", resource) when { principal.hid != resource.hid };
forbid(principal in Principal::"HouseholdMembers", action in Action::"ExportPlan", resource) when { principal.hid != resource.hid };
forbid(principal in Principal::"HouseholdMembers", action in Action::"ManagePeople", resource) when { principal.hid != resource.hid };
