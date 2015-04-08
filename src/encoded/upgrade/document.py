from past.builtins import basestring
from ..migrator import upgrade_step
from .shared import ENCODE2_AWARDS, REFERENCES_UUID


def fix_reference(value):
    if not isinstance(value, basestring):
        raise ValueError(value)
    return value.replace('PUBMED:', 'PMID:').replace(' ', '')


@upgrade_step('document', '', '2')
def document_0_2(value, system):
    # http://redmine.encodedcc.org/issues/1259

    if 'references' in value:
        value['references'] = [fix_reference(v) for v in value['references']]


@upgrade_step('document', '2', '3')
def document_2_3(value, system):
    # http://redmine.encodedcc.org/issues/1295
    # http://redmine.encodedcc.org/issues/1307

    if 'status' in value:
        if value['status'] == 'DELETED':
            value['status'] = 'deleted'
        elif value['status'] == 'CURRENT':
            if value['award'] in ENCODE2_AWARDS:
                value['status'] = 'released'
            elif value['award'] not in ENCODE2_AWARDS:
                value['status'] = 'in progress'


@upgrade_step('document', '3', '4')
def document_3_4(value, system):
    # http://redmine.encodedcc.org/issues/2591
    if 'references' in value:
        new_references = []
        for ref in value['references']:
            new_references.append(REFERENCES_UUID[ref])
        value['references'] = new_references
