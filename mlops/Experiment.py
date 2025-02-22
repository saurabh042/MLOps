import mlflow
import os
import configparser
import docker
from docker.errors import BuildError
from minio import Minio
from mlops.ProjectFile import ProjectFile
# import paramiko
import logging

logging.basicConfig(format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger('mlops.Experiment')

class Experiment:

    def __init__(self, config_path='config.cfg', use_localhost=False, verbose=True):

        self.config = None
        self.artifact_path = None
        self.remote_server_uri = None
        self.config_path = config_path
        self.use_localhost = use_localhost
        self.config_setup()
        self.build_project_file()


        self.experiment_name = self.config['project']['NAME'].lower()
        self.experiment_id = self.init_experiment()
        if verbose:
            self.print_experiment_info()

    def config_setup(self):

        self.read_config()
        self.artifact_path = self.config['server']['ARTIFACT_PATH']
        self.remote_server_uri = self.config['server']['REMOTE_SERVER_URI']

        # OS ENV CONFIG
        if self.use_localhost:
            os.environ['MLFLOW_TRACKING_URI'] = self.config['server']['LOCAL_REMOTE_SERVER_URI']
            os.environ['MLFLOW_S3_ENDPOINT_URL'] = self.config['server']['LOCAL_MLFLOW_S3_ENDPOINT_URL']
        else:
            os.environ['MLFLOW_TRACKING_URI'] = self.config['server']['REMOTE_SERVER_URI']
            os.environ['MLFLOW_S3_ENDPOINT_URL'] = self.config['server']['MLFLOW_S3_ENDPOINT_URL']

        os.environ['AWS_ACCESS_KEY_ID'] = self.config['user']['AWS_ACCESS_KEY_ID']
        os.environ['AWS_SECRET_ACCESS_KEY'] = self.config['user']['AWS_SECRET_ACCESS_KEY']

    def read_config(self):
        self.config = configparser.ConfigParser()
        self.config.read(self.config_path)

    def init_experiment(self):

        experiment = mlflow.get_experiment_by_name(self.experiment_name)
        self.configure_minio()

        if experiment is None:
            exp_id = mlflow.create_experiment(self.experiment_name, artifact_location=self.artifact_path)
            logger.info('Creating experiment: name: {0} *** ID: {1}'.format(self.experiment_name, exp_id))
        else:
            exp_id = experiment.experiment_id
            logger.info('Logging to existing experiment: {0} *** ID: {1}'.format(self.experiment_name, exp_id))

        return exp_id

    def print_experiment_info(self):
        experiment = mlflow.get_experiment(self.experiment_id)
        logger.info("Name: {}".format(experiment.name))
        logger.info("Experiment_id: {}".format(experiment.experiment_id))
        logger.info("Artifact Location: {}".format(experiment.artifact_location))
        logger.info("Tags: {}".format(experiment.tags))
        logger.info("Lifecycle_stage: {}".format(experiment.lifecycle_stage))

    def configure_minio(self):
        uri_formatted = self.config['server']['MLFLOW_S3_ENDPOINT_URL'].replace("http://", "")
        user = self.config['user']['AWS_ACCESS_KEY_ID']
        password = self.config['user']['AWS_SECRET_ACCESS_KEY']
        client = Minio(uri_formatted, user, password, secure=False)
        # if mlflow bucket does not exist, create it
        if 'mlflow' not in (bucket.name for bucket in client.list_buckets()):
            logger.info('Creating S3 bucket ''mlflow''')
            client.make_bucket("mlflow")

    def build_experiment_image(self, path: str = '.'):
        logger.info('Building experiment image ...')
        buildargs = {}
        buildargs['HTTP_PROXY'] = os.getenv('HTTP_PROXY')
        buildargs['HTTPS_PROXY'] = os.getenv('HTTPS_PROXY')

        client = docker.from_env()
        client.images.build(path=path, tag=self.experiment_name, buildargs=buildargs)

    def build_project_file(self):
        logger.info('Building project file')
        projectfile = ProjectFile(self.config)
        projectfile.generate_yaml()

    def run(self, remote: str = None, **kwargs):
        num_retries = 1
        logger.info('Starting experiment ...')

        docker_args_default = {'network': "host",
                               'gpus': 'all',
                               'ipc': 'host',
                               'rm': '',
                               'runtime': 'nvidia'}

        # update docker_args_default with values passed by project
        if 'docker_args' in kwargs:
            docker_args_default.update(kwargs['docker_args'])
            kwargs['docker_args'] = docker_args_default

        for attempt in range(num_retries):
            try:
                if remote is not None:
                    # send instruction over SSH to run on remote location
                    # todo
                    pass
                else:
                    mlflow.run('.',
                               experiment_id=self.experiment_id,
                               use_conda=False,
                               **kwargs)

            except BuildError as error:
                if attempt <= (num_retries):
                    logger.info("BuildError -- attempting to build experiment image ...")
                    self.build_experiment_image()
                else:
                    raise error
