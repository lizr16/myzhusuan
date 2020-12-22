#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
import os
import time

import tensorflow as tf
from six.moves import range
import numpy as np
import zhusuan as zs
import sys
print(sys.path)
#sys.path.append("/Users/zhuoranli/Downloads/zhusuan/examples/")
#os.environ['KMP_DUPLICATE_LIB_OK']='True'
sys.path.append("/home/lizhuoran/zhusuan/examples/")
#from examples import conf
#from examples.utils import dataset, save_image_collections
import conf
from utils import dataset, save_image_collections

@zs.meta_bayesian_net(scope="gen", reuse_variables=True)
def build_gen(x_dim, z_dim, y, n, n_particles=1):
    bn = zs.BayesianNet()
    z_mean = tf.zeros([n, z_dim])
    y = tf.reshape(y, [1, tf.shape(y)[0], 10])
    y = tf.tile(y, [n_particles, 1, 1])
    #print(z_mean.shape)
    #z_mean: Tensor("gen/zeros:0", shape=(?, 40), dtype=float32) (?, 40)
    z = bn.normal("z", z_mean, std=1., group_ndims=1, n_samples=n_particles)
    #print(z)
    #print(z.shape)
    #z: <zhusuan.framework.bn.StochasticTensor object at 0x7fd5d9af4518> (?, ?, 40)
    # y_dim = 10
    # y = tf.zeros([1, y_dim])
    #temp = tf.concat(y,z)
    temp = tf.concat([y, z], axis=2)
    h = tf.layers.dense(temp, 500, activation=tf.nn.relu)
    h = tf.layers.dense(h, 500, activation=tf.nn.relu)
    x_logits = tf.layers.dense(h, x_dim)
    #print(x_logits.shape)
    bn.deterministic("x_mean", tf.sigmoid(x_logits))
    bn.bernoulli("x", x_logits, group_ndims=1)
    return bn


@zs.reuse_variables(scope="q_net")
def build_q_net(x, z_dim, y, n_z_per_x):
    bn = zs.BayesianNet()
    h = tf.layers.dense(tf.concat([tf.cast(x, tf.float32), y], axis=-1), 500, activation=tf.nn.relu)
    h = tf.layers.dense(h, 500, activation=tf.nn.relu)
    z_mean = tf.layers.dense(h, z_dim)
    z_logstd = tf.layers.dense(h, z_dim)
    bn.normal("z", z_mean, logstd=z_logstd, group_ndims=1, n_samples=n_z_per_x)
    return bn


def main():
    # Load MNIST
    data_path = os.path.join(conf.data_dir, "mnist.pkl.gz")
    x_train, t_train, x_valid, t_valid, x_test, t_test = \
        dataset.load_mnist_realval(data_path)
    x_train = np.vstack([x_train, x_valid])
    y_train = np.vstack([t_train, t_valid])
    x_test = np.random.binomial(1, x_test, size=x_test.shape)
    x_dim = x_train.shape[1]
    y_dim = y_train.shape[1]
    # Define model parameters
    z_dim = 40

    # Build the computation graph
    n_particles = tf.placeholder(tf.int32, shape=[], name="n_particles")
    x_input = tf.placeholder(tf.float32, shape=[None, x_dim], name="x")
    x = tf.cast(tf.less(tf.random_uniform(tf.shape(x_input)), x_input),tf.int32)
    y = tf.placeholder(tf.float32, shape=[None, 10], name="y")
    n = tf.placeholder(tf.int32, shape=[], name="n")

    model = build_gen(x_dim, z_dim, y, n, n_particles)
    variational = build_q_net(x, z_dim, y, n_particles)

    lower_bound = zs.variational.elbo(
        model, {"x": x}, variational=variational, axis=0)
    cost = tf.reduce_mean(lower_bound.sgvb())
    lower_bound = tf.reduce_mean(lower_bound)

    # # Importance sampling estimates of marginal log likelihood
    is_log_likelihood = tf.reduce_mean(
        zs.is_loglikelihood(model, {"x": x}, proposal=variational, axis=0))

    optimizer = tf.train.AdamOptimizer(learning_rate=0.001)
    infer_op = optimizer.minimize(cost)

    # Random generation
    y_observe = tf.placeholder(tf.float32, shape=[None, 10], name="y_observe")
    x_gen = tf.reshape(model.observe(y=y_observe)["x_mean"], [-1, 28, 28, 1])

    # Define training/evaluation parameters
    epochs = 3000
    batch_size = 128
    iters = x_train.shape[0] // batch_size
    save_freq = 10
    test_freq = 10
    test_batch_size = 400
    test_iters = x_test.shape[0] // test_batch_size
    result_path = "results/vae"

    # Run the inference
    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())

        for epoch in range(1, epochs + 1):
            time_epoch = -time.time()
            np.random.shuffle(x_train)
            lbs = []
            for t in range(iters):
                x_batch = x_train[t * batch_size:(t + 1) * batch_size]
                y_batch = y_train[t * batch_size:(t + 1) * batch_size]
                _, lb = sess.run([infer_op, lower_bound],
                                 feed_dict={x_input: x_batch,
                                            y: y_batch,
                                            n_particles: 1,
                                            n: batch_size})
                lbs.append(lb)
            time_epoch += time.time()
            print("Epoch {} ({:.1f}s): Lower bound = {}".format(
                epoch, time_epoch, np.mean(lbs)))

            if epoch % test_freq == 0:
                time_test = -time.time()
                test_lbs, test_lls = [], []
                for t in range(test_iters):
                    test_x_batch = x_test[t * test_batch_size:(t + 1) * test_batch_size]
                    test_y_batch = t_test[t * test_batch_size:(t + 1) * test_batch_size]
                    test_lb = sess.run(lower_bound,
                                       feed_dict={x: test_x_batch,
                                                  y: test_y_batch,
                                                  n_particles: 1,
                                                  n: test_batch_size})
                    test_ll = sess.run(is_log_likelihood,
                                       feed_dict={x: test_x_batch,
                                                  y: test_y_batch,
                                                  n_particles: 1000,
                                                  n: test_batch_size})
                    test_lbs.append(test_lb)
                    test_lls.append(test_ll)
                time_test += time.time()
                print(">>> TEST ({:.1f}s)".format(time_test))
                print(">> Test lower bound = {}".format(np.mean(test_lbs)))
                print('>> Test log likelihood (IS) = {}'.format(
                    np.mean(test_lls)))

            if epoch % save_freq == 0:
                y_index = np.repeat(np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]), 10)
                y_index = np.eye(10)[y_index.reshape(-1)]
                images = sess.run(x_gen, feed_dict={y_observe: y_index, y: y_index, n: 100, n_particles: 1})
                name = os.path.join(result_path, "c-vae.epoch.{}.png".format(epoch))
                save_image_collections(images, name)


if __name__ == "__main__":
    main()
