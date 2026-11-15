"""Gemini transport contract; no real keys, external calls or quota consumed."""
import json
import os
import unittest
from datetime import date
from unittest.mock import patch
import httpx
from platform_api.agents.planner import GeminiPlanner,DemoPlanner,PlannerUnavailable,configured_planner,GEMINI_DEFAULT_MODEL
from platform_api.agents.schemas import DEMO_GOAL,Proposal

class GeminiTests(unittest.TestCase):
    def body(self):
        proposal=DemoPlanner().propose(DEMO_GOAL,date(2026,11,14)).proposal
        return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':proposal.model_dump_json()}]}}],
                'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':200,'thoughtsTokenCount':5}}

    def test_request_schema_header_and_usage_contract(self):
        captured=[]
        def handler(req):captured.append(req);return httpx.Response(200,json=self.body())
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result=GeminiPlanner(client=client,api_key='test-secret',model='models/gemini-fixture').propose(DEMO_GOAL,date(2026,11,14))
        self.assertEqual(result.provider,'gemini');self.assertEqual(result.model,'gemini-fixture')
        self.assertEqual(result.usage,{'input_tokens':100,'output_tokens':205})
        self.assertEqual(len(captured),1)
        self.assertNotIn('test-secret',str(captured[0].url))
        self.assertEqual(captured[0].headers['x-goog-api-key'],'test-secret')
        body=json.loads(captured[0].content)
        self.assertEqual(body['generationConfig']['responseJsonSchema'],Proposal.model_json_schema())
        self.assertNotIn('tools',body);self.assertNotIn('tenant',body['contents'][0]['parts'][0]['text'])
        self.assertNotIn('test-secret',captured[0].content.decode())

    def test_http_errors_are_redacted_and_not_retried(self):
        for status in (401,403,404,429,500):
            calls=[]
            def handler(req):calls.append(req);return httpx.Response(status,json={'error':{'message':'private-secret'}})
            with self.subTest(status=status),httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(PlannerUnavailable) as error:
                    GeminiPlanner(client=client,api_key='test-secret').propose(DEMO_GOAL,date(2026,11,14))
                self.assertNotIn('private-secret',str(error.exception));self.assertNotIn('test-secret',str(error.exception))
                self.assertEqual(len(calls),1)
                if status==429:self.assertIn('quota',str(error.exception))

    def test_refusal_truncation_and_missing_candidate_are_not_plans(self):
        for value in ({'promptFeedback':{'blockReason':'SAFETY'}},{'candidates':[]},
                      {'candidates':[{'finishReason':'MAX_TOKENS'}]},
                      {'candidates':[{'finishReason':'SAFETY'}]}):
            with self.subTest(response=value),httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=value))) as client:
                with self.assertRaises(PlannerUnavailable):GeminiPlanner(client=client,api_key='test').propose(DEMO_GOAL,date(2026,11,14))

    def test_malformed_schema_output_is_not_returned(self):
        value=self.body();value['candidates'][0]['content']['parts']=[{'text':'{"tenant_id":"beta","tasks":[]}'}]
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=value))) as client:
            with self.assertRaises(PlannerUnavailable):GeminiPlanner(client=client,api_key='test').propose(DEMO_GOAL,date(2026,11,14))

    def test_private_thought_parts_are_ignored(self):
        value=self.body();value['candidates'][0]['content']['parts'].insert(0,{'thought':True,'text':'private-reasoning'})
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=value))) as client:
            result=GeminiPlanner(client=client,api_key='test').propose(DEMO_GOAL,date(2026,11,14))
        self.assertNotIn('private-reasoning',result.proposal.model_dump_json())

    def test_model_url_and_missing_key_validation(self):
        for model in ('../evil','foo?key=secret','https://other.example','x/another-model'):
            with self.subTest(model=model),self.assertRaises(PlannerUnavailable):GeminiPlanner(model=model,api_key='test')
        with patch.dict(os.environ,{'GEMINI_API_KEY':''}):
            with self.assertRaises(PlannerUnavailable):GeminiPlanner()

    def test_provider_selection_and_default_model(self):
        with patch.dict(os.environ,{'PLANNER_PROVIDER':'gemini','GEMINI_API_KEY':'test','GEMINI_MODEL':''}):
            planner=configured_planner()
        self.assertIsInstance(planner,GeminiPlanner);self.assertEqual(planner.model,GEMINI_DEFAULT_MODEL)

if __name__=='__main__':unittest.main()
