import json

class BaseCRUDTestCaseMixin:
    app = None
    model_api = None

    @property
    def prefix(self):
        return self.model_api.full_prefix

    def create_fixtures(self):
        pass

    def get_create_obj_fixture(self):
        return {}

    def get_update_obj_fixture(self):
        return {}

    def test_basic_crud(self):
        obj = self.get_create_obj_fixture()
        resp = self.app.test_client().post(f'/{self.prefix}',
                                         data=json.dumps(obj))

        self.assertEqual(200, resp.status_code, 'create fail')
        created = json.loads(resp.data)
        for k, v in obj.items():
            self.assertEqual(v, created.get(k), 'created is not valid')

        resp = self.app.test_client().get(f'/{self.prefix}/{created["id"]}')
        self.assertEqual(200, resp.status_code, 'get by id fail')
        retrieved = json.loads(resp.data)
        self.assertDictEqual(created, retrieved, 'retrieved is not valid')

        update_obj = self.get_update_obj_fixture()

        resp = self.app.test_client().put(f'/{self.prefix}/{created["id"]}',
                                         data=json.dumps(update_obj))

        self.assertEqual(200, resp.status_code, 'update fail')
        retrieved = json.loads(resp.data)
        for k, v in update_obj.items():
            self.assertEqual(v, retrieved.get(k), 'updated is not valid')

        resp = self.app.test_client().delete(f'/{self.prefix}/{created["id"]}')

        self.assertEqual(200, resp.status_code, 'delete fail')

        resp = self.app.test_client().get(f'/{self.prefix}/{created["id"]}')

        self.assertEqual(404, resp.status_code, 'delete fail')

    def test_hard_delete(self):
        obj = self.get_create_obj_fixture()
        resp = self.app.test_client().post(f'/{self.prefix}',
                                         data=json.dumps(obj))

        self.assertEqual(200, resp.status_code, 'create fail')
        created = json.loads(resp.data)

        resp = self.app.test_client().delete(
            f'/{self.prefix}/{created["id"]}/hard-delete')

        self.assertEqual(200, resp.status_code, 'hard delete fail')

        with self.app.app_context():
            obj = self.model_api.model.query.get_with_deleted(created['id'])

        self.assertIsNone(obj)

    def get_list(self):
        for i in range(10):
            obj = self.model_api.model()
            obj.populate(self.get_create_obj_fixture())
            self.model_api.db.session.add(obj)
        self.model_api.db.session.commit()

        resp = self.app.test_client().get(f'/{self.prefix}/')

        self.assertEqual(200, resp.status_code)
        result = json.loads(resp.data)
        self.assertEqual(10, len(result))
