# encoding: utf-8

from __future__ import unicode_literals

from datetime import datetime
from collections import OrderedDict

from web.auth import user
from web.core import Controller, HTTPMethod, request
from web.core.locale import _
from web.core.http import HTTPFound, HTTPNotFound, HTTPForbidden

from brave.core.character.model import EVECharacter, EVECorporation, EVEAlliance
from brave.core.group.model import Group
from brave.core.group.acl import ACLList, ACLKey, ACLTitle, ACLRole, ACLMask
from brave.core.util import post_only
from brave.core.util.predicate import authorize, is_administrator
from brave.core.permission.util import user_has_permission
from brave.core.permission.model import Permission, WildcardPermission, GRANT_WILDCARD

import json

log = __import__('logging').getLogger(__name__)

class OneGroupController(Controller):
    def __init__(self, id):
        super(OneGroupController, self).__init__()

        try:
            self.group = Group.objects.get(id=id)
        except Group.DoesNotExist:
            raise HTTPNotFound()
    
    @user_has_permission('core.group.edit.requests.{gid}', gid='self.group.id')
    def accept_request(self, name):
        c = EVECharacter.objects(name=name).first()
        if not c:
            return 'json:', dict(success=False, message=_("Character with that name not found."))
            
        if not c in self.group.requests:
            return 'json:', dict(success=False, message=_("Character with that name has no request to join this group."))
        
        log.info("Adding {0} to group {1} via REQUEST_ACCEPT approved by {2}".format(c.name, self.group.id, user.primary))
        self.group.add_request_member(c)
        self.group.requests.remove(c)
        self.group.save()
        
    @user_has_permission('core.group.edit.requests.{gid}', gid='self.group.id')
    def deny_request(self, name):
        c = EVECharacter.objects(name=name).first()
        if not c:
            return 'json:', dict(success=False, message=_("Character with that name not found."))
            
        if not c in self.group.requests:
            return 'json:', dict(success=False, message=_("Character with that name has no request to join this group."))
        
        log.info("Rejecting {0}'s application to group {1} via REQUEST_DENY by {2}".format(c.name, self.group.id, user.primary))
        self.group.requests.remove(c)
        self.group.save()
        
    @user_has_permission('core.group.edit.members.{gid}', gid='self.group.id')
    def kick_member(self, name, method):
        c = EVECharacter.objects(name=name).first()
        if not c:
            return 'json:', dict(success=False, message=_("Character with that name not found."))
            
        if not c in getattr(self.group, method+"_members"):
            return 'json:', dict(success=False, message=_("Character with that name is not a member via that method."))
            
        glist = getattr(self.group, method+"_members")
        log.info("Removing {0} from group {1} (admitted via {2}) via KICK_MEMBER by {3}".format(c.name, self.group.id, method, user.primary))
        glist.remove(c)
        self.group.save()
        
    @user_has_permission('core.group.view.{gid}', gid='self.group.id')
    def index(self, rule_set=None):
        return 'brave.core.group.template.group', dict(
            area='group',
            group=self.group,
            rule_set=rule_set,
        )

    @post_only
    @user_has_permission('core.group.edit.acl.{gid}', gid='self.group.id')
    def set_rules(self, rules, really=False, rule_set=None):
        rules = json.loads(rules)
        rule_objects = []
        log.debug(rules)
        log.debug(really)

        def listify(rule, field):
            if field not in rule:
                rule[field] = []
            elif not isinstance(r[field], list):
                rule[field] = [rule[field]]

        for r in rules:
            grant = r['grant'] == "true"
            inverse = r['inverse'] == "true"
            if r['type'] == "list":
                listify(r, 'names')
                cls = ACLList.target_class(r['kind'])
                ids = [cls.objects(name=name).first().identifier for name in r['names']]
                rule_objects.append(ACLList(grant=grant, inverse=inverse, kind=r['kind'], ids=ids))
            elif r['type'] == "key":
                rule_objects.append(ACLKey(grant=grant, inverse=inverse, kind=r['kind']))
            elif r['type'] == "title":
                listify(r, 'titles')
                rule_objects.append(ACLTitle(grant=grant, inverse=inverse, titles=r['titles']))
            elif r['type'] == "role":
                listify(r, 'roles')
                rule_objects.append(ACLRole(grant=grant, inverse=inverse, roles=r['roles']))
            elif r['type'] == "mask":
                rule_objects.append(ACLMask(grant=grant, inverse=inverse, mask=r['mask']))

        log.debug(rule_objects)

        if not really:
            log.debug("not really")
            return "json:", "\n".join([r.human_readable_repr() for r in rule_objects])

        log.debug("really!")
        if rule_set == "request":
            self.group.request_rules = rule_objects
        elif rule_set == "join":
            self.group.join_rules = rule_objects
        else:
            self.group.rules = rule_objects
        success = self.group.save()
        log.debug(success)
        if success:
            return 'json:', dict(success=True)
        return 'json:', dict(success=True,
                             message=_("unimplemented"))
                             
    @post_only
    @user_has_permission('core.group.edit.perms.{gid}', gid='self.group.id')
    @user_has_permission('core.permission.grant.{permID}', permID='permission')
    def add_perm(self, permission=None):
        p = Permission.objects(id=permission)
        if len(p):
            p = p.first()
        else:
            if GRANT_WILDCARD in permission:
                p = WildcardPermission(permission)
            else:
                p = Permission(permission)
            p.save()
        self.group._permissions.append(p)
        self.group.save()
        
    @post_only
    @user_has_permission('core.group.edit.perms.{gid}', gid='self.group.id')
    @user_has_permission('core.permission.revoke.{permID}', permID='permission')
    def delete_perm(self, permission=None):
        p = Permission.objects(id=permission).first()
        self.group._permissions.remove(p)
        self.group.save()

    @post_only
    @user_has_permission('core.group.delete.{gid}', gid='self.group.id')
    def delete(self):
        self.group.delete()
        return 'json:', dict(success=True)

class GroupList(HTTPMethod):
    def get(self):
        groups = sorted(Group.objects(), key=lambda g: g.category.id if g.category else g.id)
        
        visibleGroups = list()
        joinableGroups = list()
        requestableGroups = list()
        categories = list()
        
        if not user.primary:
            return 'brave.core.group.template.list_groups', dict(
            area='group',
            groups=visibleGroups
        )
        
        for g in groups:
            if g.evaluate(user, user.primary, rule_set="main"):
                continue
            elif g.evaluate(user, user.primary, rule_set='join'):
                joinableGroups.append(g)
                visibleGroups.append(g)
                categories.append(g.category)
            elif g.evaluate(user, user.primary, rule_set='request'):
                requestableGroups.append(g)
                visibleGroups.append(g)
                categories.append(g.category)
                
        mapping = dict()
        
        for c in categories:
            mapping[c] = list(Group.objects(category__id=c.id if c else None))
        
        def ranksort(i):
            if i:
                return i.rank
            else:
                return 10000
        
        mapping = OrderedDict((i, mapping[i]) for i in sorted(mapping.keys(), key=ranksort))
        
        return 'brave.core.group.template.list_groups', dict(
            area='group',
            groups=visibleGroups,
            joinableGroups=joinableGroups,
            requestableGroups=requestableGroups,
            categories=mapping,
        )
        
    def leave(self, group):
        log.info("Removing {0} from group {1} via LEAVE.".format(user.primary, group.id))
        if user.primary in group.join_members:
            group.join_members.remove(user.primary)
        if user.primary in group.request_members:
            group.request_members.remove(user.primary)
            
        group.save()
        return 'json:', dict(success=True)
        
    def join(self, group):
        if not group.evaluate(user, user.primary, rule_set='join'):
            return 'json:', dict(success=False, message=_("You do not have permission to join this group."))
        
        log.info("Adding {0} to group {1} via JOIN.".format(user.primary, group.id))
        group.add_join_member(user.primary)
        
        group.save()
        return 'json:', dict(success=True)
        
    def request(self, group):
        if not group.evaluate(user, user.primary, rule_set='request'):
            return 'json:', dict(success=False, message=_("You do not have permission to request access to this group."))
        
        log.info("Adding {0} to requests list of {1} via REQUEST.".format(user.primary, group.id))
        group.add_request(user.primary)
        
        group.save()
        return 'json:', dict(success=True)
        
    def withdraw(self, group):
        log.info("Removing {0} from requests list of {1} via WITHDRAW.".format(user.primary, group.id))
        group.requests.remove(user.primary)
        
        group.save()
        return 'json:', dict(success=True)

    def post(self, id=None, action=None):
        if not action:
            return 'json:', dict(success=False)
        else:
            group = Group.objects(id=id).first()
            
            if not group:
                return 'json:', dict(success=False, message=_("Group not found"))
            
            return getattr(self, action)(group)

class ManageGroupList(HTTPMethod):
    @user_has_permission('core.group.view.*', accept_any_matching=True)
    def get(self):
        groups = sorted(Group.objects(), key=lambda g: g.id)
        
        visibleGroups = list()
        for g in groups:
            if user.has_permission('core.group.view.'+g.id):
                visibleGroups.append(g)
        
        return 'brave.core.group.template.manage_groups', dict(
            area='group',
            groups=visibleGroups
        )

    @user_has_permission('core.group.create')
    def post(self, id=None, title=None):
        if not id:
            return 'json:', dict(success=False,
                                 message=_("id required"))
                                 
        if id == 'manange':
            return 'json:', dict(success=False,
                                 message=_("You cannot name a group 'manage'"))
        if not title:
            return 'json:', dict(success=False,
                                 message=_("title required"))
        g = Group.create(id, title, user)
        if not g:
            return 'json:', dict(success=False,
                                 message=_("group with that id already existed"))
        # Give the creator of the group the ability to edit it and delete it.
        editPerm = Permission('core.group.edit.acl.'+g.id, "Ability to edit ACLs for Group {0}".format(g.id))
        deletePerm = Permission('core.group.delete.'+g.id, "Ability to delete Group {0}".format(g.id))
        user.primary.personal_permissions.append(editPerm)
        user.primary.personal_permissions.append(deletePerm)
        user.save(cascade=True)
        
        return 'json:', dict(success=True, id=g.id)

class GroupController(Controller):
    index = GroupList()
    manage = ManageGroupList()

    def __lookup__(self, id, *args, **kw):
        request.path_info_pop()  # We consume a single path element.
        return OneGroupController(id), args

    @user_has_permission('core.group.edit.*', accept_any_matching=True)
    def check_rule_reference_exists(self, kind, name):
        cls = ACLList.target_class(kind)
        return "json:", dict(exists=bool(cls.objects(name=name)))
