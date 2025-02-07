# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

from __future__ import unicode_literals
import gameplan
import frappe
from frappe.utils import validate_email_address, split_emails
from gameplan.utils import validate_type


@frappe.whitelist(allow_guest=True)
def get_user_info(user=None):
	if frappe.session.user == "Guest":
		frappe.throw("Authentication failed", exc=frappe.AuthenticationError)

	filters = [
		['User', 'enabled', '=', 1],
		["Has Role", "role", "like", "Gameplan %"]
	]
	if user:
		filters.append(["User", "name", "=", user])
	users = frappe.db.get_all(
		"User",
		filters=filters,
		fields=["name", "email", "user_image", "full_name", "user_type"],
		order_by="full_name asc",
		distinct=True
	)
	# bug: order_by isn't applied when distinct=True
	users.sort(key=lambda x: x.full_name)
	roles = frappe.db.get_all('Has Role',
		filters={'parenttype': 'User'},
		fields=['role', 'parent']
	)
	user_profile_names = frappe.db.get_all('Team User Profile',
		fields=['user', 'name'],
		filters={'user': ['in', [u.name for u in users]]}
	)
	user_profile_names_map = {u.user: u.name for u in user_profile_names}
	for user in users:
		if frappe.session.user == user.name:
			user.session_user = True
		user.user_profile = user_profile_names_map.get(user.name)
		user_roles = [r.role for r in roles if r.parent == user.name]
		user.role = None
		for role in ['Gameplan Guest', 'Gameplan Member', 'Gameplan Admin']:
			if role in user_roles:
				user.role = role
	return users


@frappe.whitelist()
@validate_type
def change_user_role(user: str, role: str):
	if gameplan.is_guest():
		frappe.throw('Only Admin can change user roles')

	if role not in ['Gameplan Guest', 'Gameplan Member', 'Gameplan Admin']:
		return get_user_info(user)[0]

	user_doc = frappe.get_doc('User', user)
	for _role in user_doc.roles:
		if _role.role in ['Gameplan Guest', 'Gameplan Member', 'Gameplan Admin']:
			user_doc.remove(_role)
	user_doc.append_roles(role)
	user_doc.save(ignore_permissions=True)

	return get_user_info(user)[0]


@frappe.whitelist()
@validate_type
def remove_user(user: str):
	user_doc = frappe.get_doc('User', user)
	user_doc.enabled = 0
	user_doc.save(ignore_permissions=True)
	return user


@frappe.whitelist()
@validate_type
def invite_by_email(emails: str, role: str, projects: list = None):
	if not emails:
		return
	email_string = validate_email_address(emails, throw=False)
	email_list = split_emails(email_string)
	if not email_list:
		return
	existing_members = frappe.db.get_all('User', filters={'email': ['in', email_list]}, pluck='email')
	existing_invites = frappe.db.get_all('GP Invitation',
		filters={
			'email': ['in', email_list],
			'role': ['in', ['Gameplan Admin', 'Gameplan Member']]
		},
		pluck='email')
	to_invite = list(set(email_list) - set(existing_members) - set(existing_invites))
	if projects:
		projects = frappe.as_json(projects, indent=None)

	for email in to_invite:
		frappe.get_doc(
			doctype='GP Invitation',
			email=email,
			role=role,
			projects=projects
		).insert(ignore_permissions=True)


@frappe.whitelist()
def unread_notifications():
	res = frappe.db.get_all('Team Notification', 'count(name) as count', {'to_user': frappe.session.user, 'read': 0})
	return res[0].count


@frappe.whitelist(allow_guest=True)
@validate_type
def accept_invitation(key: str = None):
	if not key:
		frappe.throw("Invalid or expired key")

	result = frappe.db.get_all(
		"GP Invitation", filters={"key": key}, pluck='name'
	)
	if not result:
		frappe.throw("Invalid or expired key")

	invitation = frappe.get_doc('GP Invitation', result[0])
	invitation.accept()
	invitation.reload()

	if invitation.status == "Accepted":
		frappe.local.login_manager.login_as(invitation.email)
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = "/g"


@frappe.whitelist()
def get_unsplash_photos(keyword=None):
	from gameplan.unsplash import get_list, get_by_keyword

	if keyword:
		return get_by_keyword(keyword)

	return frappe.cache().get_value("unsplash_photos", generator=get_list)


@frappe.whitelist()
def get_unread_items():
	from frappe.query_builder.functions import Count
	Discussion = frappe.qb.DocType("Team Discussion")
	Visit = frappe.qb.DocType("Team Discussion Visit")
	query = (
		frappe.qb.from_(Discussion)
			.select(Discussion.team, Count(Discussion.team).as_("count"))
			.left_join(Visit)
			.on((Visit.discussion == Discussion.name) & (Visit.user == frappe.session.user))
			.where((Visit.last_visit.isnull()) | (Visit.last_visit < Discussion.last_post_at))
			.groupby(Discussion.team)
	)
	is_guest = gameplan.is_guest()
	if is_guest:
		GuestAccess = frappe.qb.DocType('GP Guest Access')
		project_list = GuestAccess.select(GuestAccess.project).where(GuestAccess.user == frappe.session.user)
		query = query.where(Discussion.project.isin(project_list))

	data = query.run(as_dict=1)
	out = {}
	for d in data:
		out[d.team] = d.count
	return out















@frappe.whitelist()
def onboarding(data):
	data = frappe.parse_json(data)
	team = frappe.get_doc(doctype='Team', title=data.team).insert()
	frappe.get_doc(doctype='Team Project', team=team.name, title=data.project).insert()
	emails = ', '.join(data.emails)
	invite_by_email(emails, role='Gameplan Member')
	return team.name

@frappe.whitelist(allow_guest=True)
def oauth_providers():
	from frappe.utils.html_utils import get_icon_html
	from frappe.utils.password import get_decrypted_password
	from frappe.utils.oauth import get_oauth2_authorize_url, get_oauth_keys

	out = []
	providers = frappe.get_all(
		"Social Login Key",
		filters={"enable_social_login": 1},
		fields=["name", "client_id", "base_url", "provider_name", "icon"],
		order_by="name",
	)

	for provider in providers:
		client_secret = get_decrypted_password("Social Login Key", provider.name, "client_secret")
		if not client_secret:
			continue

		icon = None
		if provider.icon:
			if provider.provider_name == "Custom":
				icon = get_icon_html(provider.icon, small=True)
			else:
				icon = f"<img src='{provider.icon}' alt={provider.provider_name}>"

		if provider.client_id and provider.base_url and get_oauth_keys(provider.name):
			out.append(
				{
					"name": provider.name,
					"provider_name": provider.provider_name,
					"auth_url": get_oauth2_authorize_url(provider.name, '/g/home'),
					"icon": icon,
				}
			)
	return out
