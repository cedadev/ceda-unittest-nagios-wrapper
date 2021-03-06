#!/usr/bin/env python
"""Classes to wrap unittest test case as a Nagios script
"""
__author__ = "P J Kershaw"
__date__ = "21/11/17"
__copyright__ = "Copyright 2018 United Kingdom Research and Innovation"
__license__ = """BSD - See LICENSE file in top-level directory"""
__contact__ = "Philip.Kershaw@stfc.ac.uk"
import sys
import os
import inspect
import unittest
import logging
from argparse import ArgumentParser

import nagiosplugin
from slack_logging_handler.handler import SlackHandler

log = logging.getLogger('nagiosplugin')

# There doesn't seem to be a way of getting this list through logging's public
# interface - resorting to '_' 'protected' var
LOG_LEVEL_NAMES = logging._nameToLevel.keys()
LOG_LEVEL_NAMES_STR = ', '.join(LOG_LEVEL_NAMES)
LOG_LEVEL_NAMES_OPT_STR = '|'.join(LOG_LEVEL_NAMES)


class UnittestCaseContext(nagiosplugin.context.Context):
    '''Nagios Context - sets tests to run and executes them'''

    def __init__(self, *args, **kwargs):
        '''Overload in order to obtain module name for unittests'''
        self._unittestcase_class = kwargs.pop('unittestcase_class', None)
        super(UnittestCaseContext, self).__init__(*args, **kwargs)

    def evaluate(self, metric, resource):
        '''Run tests from input unittest case'''
        # The test may be an individual one or a whole test case.  For the
        # latter, this may involve multiple tests
        test_name = metric[0]

        test_suite = unittest.defaultTestLoader.loadTestsFromTestCase(
                                                    self._unittestcase_class)
        
        result = unittest.TestResult()
        test_suite.run(result)

        n_failures = len(result.failures)
        n_errors = len(result.errors)
        n_problems = n_failures + n_errors

        # If the whole test case is run then multiple tests will be executed
        # so need to cater for multiple results:
        if n_problems > 0:
            if result.testsRun == n_problems:
                # Overall fail
                status = nagiosplugin.context.Critical
                hint = 'All tests failed: '
            else:
                # Overall warning
                status = nagiosplugin.context.Warn
                hint = 'Some tests failed: '

            log.critical('{} failed'.format(self.describe(metric)))
            
            # Log all the rest
            hint_list = []
            for error in result.errors:
                log.error(error[0])
                log.debug('', extra={'slack_exception_attachment':error[1]})
                
                hint_list.append(str(error[0]))
                
            # Log all the rest
            for failure in result.failures:
                log.error(failure[0])
                log.debug('', extra={'slack_exception_attachment':failure[1]})
                
                hint_list.append(str(failure[0]))
                
            hint += ', '.join(hint_list)
        else:
            # Overall pass
            status = nagiosplugin.context.Ok
            hint = '{} test passed'.format(test_name)

        return self.result_cls(status, hint=hint, metric=metric)


class UnittestCaseResource(nagiosplugin.Resource):
    '''Nagios resource abstraction for unittest case
    '''
    def __init__(self, test_names):
        '''Overload to pass special test_names parameter'''
        super(UnittestCaseResource, self).__init__()

        self.test_names = test_names

    def probe(self):
        '''Special probe method applies the metrics for the resource'''
        for test_name in self.test_names:
            yield nagiosplugin.Metric(test_name, True,
                                      context='UnittestCaseContext')


class UnittestCaseResultsSummary(nagiosplugin.Summary):
    """Present output summary
    """
    def ok(self, results):
        msg = ', '.join([result.hint for result in results])
        log.info(msg)
        return msg

    def problem(self, results):
        msg = ', '.join([result.hint for result in results])
        log.info(msg)
        return msg


class UnittestNagiosWrapperError(Exception):
    '''Base class for unittest Nagios wrapper exceptions'''
    
    
class UnittestNagiosWrapperConfigError(UnittestNagiosWrapperError):
    '''Configuration error with unittest Nagios wrapper'''
    
    
@nagiosplugin.guarded
def nagios_script(unittestcase_class, check_name=None, log_level=logging.WARN,
                  slack_webhook_url=None, slack_channel=None, 
                  slack_user=None):
    '''Top-level function for script'''

    # All the possible test names which can be invoked from the unittest
    # TestCase
    test_names = ['{}.{}'.format(unittestcase_class.__name__, name_)
                      for name_ in dir(unittestcase_class)
                      if name_.startswith('test')]

    test_names_displ = '[' + '] ['.join(test_names) + ']'

    options = '[-h] [-s] [-c] [-u] {}'.format(test_names_displ)
    description = (
        'Nagios/Icinga wrapper script to {} unit tests.  Specify one or more '
        'of the unit test names to run or none to run all'
    ).format(unittestcase_class.__name__)

    parser = ArgumentParser(usage='%(prog)s ' + options,
                            description=description)

    slack_webhook_url_help_txt = ("Set webhook URL to log output to a Slack "
                                  "channel.")
    if slack_webhook_url:
        slack_webhook_url_help_txt += '  Defaults to "{}"'.format(
                                                            slack_webhook_url)

    log_level_s = logging._levelToName.get(log_level)
    if log_level is None:
        raise UnittestNagiosWrapperConfigError(
            'Unrecognised default log-level set.  Use one of: {}'.format(
                LOG_LEVEL_NAMES_STR))
                
    parser.add_argument("-n", "--test-name",
                        dest="check_name", default=unittestcase_class.__name__,
                        metavar="<name of test>",
                        help="Descriptive name of test - defaults to name of "
                            "unittest class: '{}'.".format(
                                unittestcase_class.__name__))
                    
    parser.add_argument("-l", "--slack-log-level",
                        dest="log_level", default=log_level_s,
                        metavar="<{}>".format(LOG_LEVEL_NAMES_OPT_STR),
                        help="Set the log-level for log messages to Slack.")
    
    parser.add_argument("-s", "--slack-webhook-url",
                        dest="slack_webhook_url", default=None,
                        metavar="<slack webhook URL>",
                        help=slack_webhook_url_help_txt)

    slack_channel_help_txt = "Destination Slack channel."
    if slack_channel:
        slack_channel_help_txt += '  Defaults to "{}"'.format(slack_channel)

    parser.add_argument("-c", "--slack-channel", dest="slack_channel",
                        default=slack_channel, metavar="<slack channel>",
                        help=slack_channel_help_txt)

    slack_user_help_txt = "Slack user for submitting logging info."
    if slack_channel:
        slack_user_help_txt += '  Defaults to "{}"'.format(slack_user)

    parser.add_argument("-u", "--slack-user", dest="slack_user",
                        default=slack_user, metavar="<slack username>",
                        help=slack_user_help_txt)

    parsed_args, selected_test_names = parser.parse_known_args()

    # Command line arguments take precedence over any passed through function
    # inputs
    if parsed_args.slack_webhook_url is not None:
        slack_webhook_url = parsed_args.slack_webhook_url

    if parsed_args.slack_channel is not None:
        slack_channel = parsed_args.slack_channel

    if parsed_args.slack_user is not None:
        slack_user = parsed_args.slack_user

    if slack_webhook_url is not None:
        log_level = logging._nameToLevel.get(parsed_args.log_level)
        if log_level is None:
            parser.error('Unrecognised log-level.  Use one of: {}'.format(
                LOG_LEVEL_NAMES_STR))
            
        log.addHandler(SlackHandler(slack_webhook_url,
                                    channel=slack_channel,
                                    username=slack_user,
                                    level=log_level))

    # If no tests are selected, default to run all by setting the unittest
    # TestCase class name
    if len(selected_test_names) == 0:
        selected_test_names = [unittestcase_class.__name__]

         
    nagios_resource = UnittestCaseResource(selected_test_names)
    nagios_context = UnittestCaseContext(
                                    'UnittestCaseContext',
                                    fmt_metric=parsed_args.check_name,
                                    unittestcase_class=unittestcase_class)

    nagios_results_summary = UnittestCaseResultsSummary()
    check = nagiosplugin.Check(nagios_resource, nagios_context,
                               nagios_results_summary)

    check.name = parsed_args.check_name
    check.main()
